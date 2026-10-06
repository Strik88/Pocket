"""Pull recordings from Pocket and write them to disk, sorted per client."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx

from . import classify as classifier
from . import meetings
from .config import Client, Project, Settings, load_settings, save_settings
from .dossier import build_dossier, rebuild_all
from .i18n import t
from .index import Index
from .pocket_api import PocketClient, legacy_speaker_labels, parse_recording
from .storage import (
    client_dir,
    meta_dir,
    parse_markdown,
    rename_speakers_in_file,
    render_markdown,
    set_location,
    speakers_in,
    target_path,
    unique_path,
)

log = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_thread_lock = threading.Lock()
# Raise this when reading Pocket's data improves: existing files are then rewritten from the stored JSON.
FORMAT_VERSION = 2


@dataclass
class SyncResult:
    started: str = ""
    finished: str = ""
    new: int = 0
    updated: int = 0
    skipped: int = 0
    pending: int = 0  # still processing in Pocket; retried next time
    errors: list[str] = field(default_factory=list)
    suggested_clients: list[str] = field(default_factory=list)
    message: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# -- State --------------------------------------------------------------------


def _state_path(settings: Settings) -> Path:
    return meta_dir(settings) / "state.json"


def load_state(settings: Settings) -> dict:
    p = _state_path(settings)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"recordings": {}, "last_sync": "", "last_result": None}


def save_state(settings: Settings, state: dict) -> None:
    p = _state_path(settings)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


class FileLock:
    """Cross-process lock so the web app and the MCP server never sync at the same time."""

    def __init__(self, path: Path, stale_after: float = 1800):
        self.path, self.stale_after, self.fd = path, stale_after, None

    def acquire(self) -> bool:
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_after:
                self.path.unlink(missing_ok=True)
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.path.unlink(missing_ok=True)


# -- Sync ---------------------------------------------------------------------


def is_running(settings: Settings) -> bool:
    return (meta_dir(settings) / "sync.lock").exists() or _thread_lock.locked()


def run_sync(
    settings: Settings | None = None,
    full: bool = False,
    progress: Callable[[str], None] | None = None,
    transport: httpx.BaseTransport | None = None,
    allow_ai: bool = True,
) -> SyncResult:
    settings = settings or load_settings()
    say = progress or (lambda msg: log.debug(msg))  # titles stay out of the log file
    result = SyncResult(started=datetime.now().isoformat(timespec="seconds"))
    if not settings.pocket_ready:
        result.message = t(settings.language, "sync_no_key")
        return result
    if settings.demo_mode and transport is None:
        from . import demo

        transport = demo.transport()

    lock = _acquire(settings)
    if not lock:
        result.message = t(settings.language, "sync_busy")
        return result
    try:
        _do_sync(settings, full, say, result, transport, allow_ai)
    finally:
        lock.release()
        _thread_lock.release()
    result.finished = datetime.now().isoformat(timespec="seconds")
    return result


def _acquire(settings: Settings) -> FileLock | None:
    """Both locks, or None when a sync is already running in this or another process."""
    if not _thread_lock.acquire(blocking=False):
        return None
    lock = FileLock(meta_dir(settings) / "sync.lock")
    if not lock.acquire():
        _thread_lock.release()
        return None
    return lock


def _do_sync(settings: Settings, full: bool, say, result: SyncResult, transport, allow_ai: bool = True) -> None:
    state = load_state(settings)
    known: dict = state.setdefault("recordings", {})
    speakers: dict = state.setdefault("speakers", {})
    index = Index(settings)
    index.refresh()  # pick up files the user moved by hand
    touched_clients: set[str] = _upgrade_files(settings, state, index)
    pending: dict = state.get("refetch_ids") or {}  # upgrade: files whose Pocket data must be fetched again

    start_date = settings.sync_since
    if not full and state.get("last_sync"):
        last = datetime.fromisoformat(state["last_sync"])
        start_date = max(start_date or "", (last - timedelta(days=3)).strftime("%Y-%m-%d"))

    raw_dir = meta_dir(settings) / "raw"
    try:
        with PocketClient(settings.pocket_api_key, settings.pocket_base_url, transport=transport) as pocket:
            say(t(settings.language, "sync_fetching"))
            items = list(pocket.iter_recordings(start_date=start_date))
            say(t(settings.language, "sync_found", n=len(items)))
            for n, item in enumerate(items, 1):
                rid = str(item.get("id") or "")
                if not _SAFE_ID.fullmatch(rid):  # ids end up in file names: never trust them blindly
                    if rid:
                        result.errors.append(f"skipped recording with an unexpected id ({len(rid)} characters)")
                    continue
                updated = str(item.get("updated_at") or item.get("updatedAt") or "")
                existing = index.get(rid)
                if rid in known and existing and known[rid].get("updated_at") == updated and updated:
                    result.skipped += 1
                    continue
                try:
                    rec = pocket.get_recording(rid)
                except Exception as exc:  # keep going; one bad recording should not stop the rest
                    result.errors.append(f"{item.get('title') or rid}: {exc}")
                    continue
                if rec.id != rid:
                    result.errors.append(f"{item.get('title') or rid}: id mismatch")
                    continue
                if not rec.has_transcript:
                    result.pending += 1
                    continue
                say(f"[{n}/{len(items)}] {rec.title}")
                if settings.keep_raw_json:
                    raw_dir.mkdir(exist_ok=True)
                    (raw_dir / f"{rid}.json").write_text(json.dumps(rec.raw, indent=1, ensure_ascii=False), encoding="utf-8")

                meeting = meetings.match_event(settings, rec.recorded_at, rec.duration_seconds) if settings.calendar_urls else None
                if settings.demo_mode:
                    meeting = _demo_meeting(rid, rec)
                done_actions: list[tuple[bool, str]] = []
                if existing:
                    path = Path(existing.path)
                    client, project = existing.client, existing.project
                    if rid in pending:  # Pocket changed it and the upgrade still waited for it
                        _migrate_names(state, rid, rec.raw)
                        _backup(settings, rid, path)
                        pending.pop(rid)
                    source, done_actions, old_meeting = _kept_from_file(path)
                    meeting = meeting or old_meeting
                    result.updated += 1
                else:
                    decision = classifier.classify(settings, rec, meeting, allow_ai=allow_ai)
                    client, project, source = decision.client, decision.project, decision.source
                    if decision.suggested_client:
                        from .discovery import add_suggestion

                        add_suggestion(settings, decision.suggested_client, rid, source)
                        result.suggested_clients.append(decision.suggested_client)
                    path = unique_path(target_path(settings, rec, client, project))
                    result.new += 1
                meeting_dict = meeting.as_dict() if hasattr(meeting, "as_dict") else meeting
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    render_markdown(settings, rec, client, source, project, meeting_dict, speakers.get(rid), done_actions),
                    encoding="utf-8",
                )
                index.upsert_file(path)
                known[rid] = {"updated_at": updated or rec.updated_at, "synced_at": datetime.now().isoformat(timespec="seconds")}
                if client:
                    touched_clients.add(client)
                if n % 10 == 0:
                    save_state(settings, state)
            _refetch(settings, state, index, pocket, pending, result, touched_clients)
    except Exception as exc:
        result.errors.append(str(exc))
        result.message = str(exc)

    if pending:
        state["refetch_ids"] = pending
    else:
        state.pop("refetch_ids", None)
    state["last_sync"] = result.started if not result.message else state.get("last_sync", "")
    state["last_result"] = result.as_dict()
    save_state(settings, state)
    after_change(settings, index, touched_clients, say)
    index.close()
    if not result.message:
        result.message = t(settings.language, "sync_done", new=result.new, updated=result.updated, pending=result.pending)
    say(result.message)


def _demo_meeting(rid: str, rec):
    from . import demo

    m = demo.meeting_for(rid)
    if not m or not rec.recorded_at:
        return None
    emails = [e.group(0).lower() for a in m.get("attendees", []) for e in [re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", a)] if e]
    end = rec.recorded_at + timedelta(seconds=rec.duration_seconds or 1800)
    return meetings.Event(title=m.get("title", ""), start=rec.recorded_at, end=end, attendees=m.get("attendees", []), emails=emails)


def after_change(settings: Settings, index: Index, clients: set[str], say=lambda m: None) -> None:
    """Refresh everything derived from the files: dossiers, Claude status notes, meaning index."""
    from . import reports, semantic

    for client in clients:
        if settings.ai_client_status and settings.ai_ready:
            try:
                reports.update_client_status(settings, index, client)
            except Exception as exc:  # optional extra; never fail a sync on it
                log.warning("client status for %s failed: %s", client, exc)
        build_dossier(settings, client, index.list(client=client, limit=100_000))
    if settings.semantic_search:
        try:
            n = semantic.update(settings, index)
            if n:
                say(t(settings.language, "semantic_indexed", n=n))
        except Exception as exc:
            log.warning("semantic index update failed: %s", exc)


def _kept_from_file(path: Path) -> tuple[str, list[tuple[bool, str]], dict | None]:
    """What a rewrite keeps from the current file: why it is filed here, its action items with their
    ticks, the meeting."""
    source = _current_source(path) or "kept"
    old = parse_markdown(path)
    done = list(old.action_items) if old else []
    meeting = None
    if old and old.meta.get("meeting"):
        meeting = {"title": old.meta.get("meeting"), "attendees": old.meta.get("attendees") or []}
    return source, done, meeting


def _backup(settings: Settings, rid: str, path: Path) -> None:
    """Keep the file as it was before an upgrade rewrites it, so notes typed into it survive. The first
    copy is never overwritten: a second run would otherwise replace it with the rewritten file."""
    folder = meta_dir(settings) / "backup"
    folder.mkdir(exist_ok=True)
    target = folder / f"{rid}.md"
    if not target.exists():
        shutil.copy2(path, target)


def _migrate_names(state: dict, rid: str, raw: dict) -> None:
    """Names given to speakers in 1.0.0 are stored under 1.0.0's labels; move them to today's labels for
    the same person. Runs once per recording, during the upgrade."""
    stored = state.get("speakers", {}).get(rid)
    mapping = legacy_speaker_labels(raw) if stored else {}
    if not mapping:
        return
    moved = {k: v for k, v in stored.items() if k not in mapping}
    moved.update({mapping[k]: v for k, v in stored.items() if k in mapping})
    state["speakers"][rid] = moved


def _apply_upgrade(settings: Settings, state: dict, index: Index, row, rec) -> bool:
    """Rewrite one existing file when it gains action items or speakers. Returns True when rewritten."""
    path = Path(row.path)
    if not rec.has_transcript or not path.exists():
        return False
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    old = parse_markdown(path)
    names = state.get("speakers", {}).get(row.pocket_id) or {}
    gains_actions = bool(rec.action_items) and not (old and old.action_items)
    shown = {names.get(seg.speaker, seg.speaker) for seg in rec.segments if seg.speaker}  # as the file would show them
    on_disk = speakers_in(current)
    gains_speakers = len(shown) > len(on_disk)
    if not (gains_actions or gains_speakers):
        # Left as it is, but speakers nobody named yet get today's label ("Speaker 0" -> "Speaker 1"),
        # so a name given later is stored under the label the next rewrite uses.
        named = set(names.values())
        stale = {o: n for o, n in legacy_speaker_labels(rec.raw).items() if o != n and o in on_disk and o not in named}
        if stale and rename_speakers_in_file(path, stale):
            index.upsert_file(path)
        return False
    source, done, meeting = _kept_from_file(path)
    try:
        _backup(settings, row.pocket_id, path)
        path.write_text(render_markdown(settings, rec, row.client, source, row.project, meeting, names, done), encoding="utf-8")
    except OSError as exc:
        log.warning("could not rewrite a recording file: %s", exc)
        return False
    index.upsert_file(path)
    return True


def _upgrade_files(settings: Settings, state: dict, index: Index) -> set[str]:
    """After the reading of Pocket's data improved (action items, speakers): bring existing files up to
    date from the stored Pocket JSON, offline. Only files that gain something are rewritten, each with a
    backup in .pocket-bridge/backup. Files without stored JSON for the version on disk are listed in
    state["refetch_ids"] and fetched one by one on the next sync. Returns the clients whose files changed."""
    if state.get("format", 1) >= FORMAT_VERSION:
        return set()
    raw_dir = meta_dir(settings) / "raw"
    pending: dict = state.setdefault("refetch_ids", {})
    touched: set[str] = set()
    for row in index.list(limit=10_000_000):
        rid = row.pocket_id or ""
        if not _SAFE_ID.fullmatch(rid):
            continue
        try:
            rec = parse_recording(json.loads((raw_dir / f"{rid}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            rec = None
        old = parse_markdown(Path(row.path))
        # The stored JSON must be the version the file was written from (it is not updated when
        # keeping raw data was switched off later); otherwise fetch the current version.
        if rec is None or rec.id != rid or not old or not rec.updated_at or rec.updated_at != str(old.meta.get("pocket_updated_at") or ""):
            pending.setdefault(rid, 0)
            continue
        _migrate_names(state, rid, rec.raw)
        if _apply_upgrade(settings, state, index, row, rec) and row.client:
            touched.add(row.client)
    if not pending:
        state.pop("refetch_ids", None)
    state["format"] = FORMAT_VERSION
    save_state(settings, state)
    return touched


def _refetch(settings: Settings, state: dict, index: Index, pocket, pending: dict, result: SyncResult, touched: set[str]) -> None:
    """Fetch the recordings the upgrade could not handle offline, one by one, and rewrite only those that
    gain something. Recordings the user deleted or archived stay gone. After three failed tries a
    recording is no longer fetched here; it stays listed so the normal sync upgrades it (speaker names
    included) when Pocket next changes it."""
    raw_dir = meta_dir(settings) / "raw"
    for rid in list(pending):
        row = index.get(rid)
        if not row or not _SAFE_ID.fullmatch(rid):
            pending.pop(rid)
            continue
        if pending[rid] >= 3:  # given up fetching; handled when Pocket next updates it (main loop)
            continue
        try:
            rec = pocket.get_recording(rid)
            if rec.id != rid:
                raise ValueError("id mismatch")
        except Exception as exc:
            pending[rid] = pending.get(rid, 0) + 1
            log.warning("upgrade: fetching a recording again failed: %s", exc)
            continue
        if settings.keep_raw_json:
            raw_dir.mkdir(exist_ok=True)
            (raw_dir / f"{rid}.json").write_text(json.dumps(rec.raw, indent=1, ensure_ascii=False), encoding="utf-8")
        _migrate_names(state, rid, rec.raw)
        if _apply_upgrade(settings, state, index, row, rec):
            result.updated += 1
            if row.client:
                touched.add(row.client)
        pending.pop(rid)
        state["refetch_ids"] = pending
        save_state(settings, state)  # progress survives quitting the app halfway


def upgrade(settings: Settings | None = None) -> int:
    """Run the file upgrade on its own (at start-up), so users see action items and speakers without
    waiting for a sync. Skips quietly when a sync is running; that sync does the upgrade itself."""
    settings = settings or load_settings()
    if not settings.root.exists():
        return 0
    state = load_state(settings)
    if state.get("format", 1) >= FORMAT_VERSION:
        return 0
    lock = _acquire(settings)
    if not lock:
        return 0
    try:
        state = load_state(settings)
        index = Index(settings)
        try:
            index.refresh()
            clients = _upgrade_files(settings, state, index)
            for client in clients:
                build_dossier(settings, client, index.list(client=client, limit=100_000))
            return len(clients)
        finally:
            index.close()
    finally:
        lock.release()
        _thread_lock.release()


def _current_source(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:20]:
            if line.startswith("client_source:"):
                return json.loads(line.split(":", 1)[1])
    except Exception:
        pass
    return ""


# -- Manual changes -------------------------------------------------------------


def ensure_client(settings: Settings, client: str | None, project: str | None) -> tuple[str | None, str | None]:
    """Normalise names to the configured client/project, creating them if new. Caller saves settings."""
    from .discovery import clean_name

    client = client.strip() if client else None
    project = project.strip() if (project and client) else None
    if client:
        cfg = settings.find_client(client)
        if not cfg:
            name = clean_name(settings, client, strip_legal=False)
            if not name:
                raise ValueError(f"invalid client name: {client}")
            cfg = settings.find_client(name) or Client(name=name)
            if cfg not in settings.clients:
                settings.clients.append(cfg)
        client = cfg.name
        if project:
            p = cfg.find_project(project)
            if p:
                project = p.name
            else:
                name = clean_name(settings, project, strip_legal=False)
                if not name:
                    raise ValueError(f"invalid project name: {project}")
                cfg.projects.append(Project(name=name))
                project = name
    return client, project


def move_row(settings: Settings, index: Index, row, client: str | None, project: str | None, source: str) -> Path:
    """Move one indexed recording to a client/project folder and record why. No dossier rebuild."""
    old_path = Path(row.path)
    year = old_path.parent.name
    target_dir = client_dir(settings, client, project) / year
    new_path = old_path if old_path.parent == target_dir else unique_path(target_dir / old_path.name)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if new_path != old_path:
        shutil.move(str(old_path), str(new_path))
    set_location(new_path, client, project, source)
    index.upsert_file(new_path)
    return new_path


def assign(settings: Settings, ref: str, client: str | None, project: str | None = None, source: str = "manual") -> Path:
    """Move a recording to another client's (project) folder, or to Unsorted with client=None."""
    index = Index(settings)
    try:
        index.refresh()
        row = index.find(ref)
        if not row:
            raise ValueError(f"Opname niet gevonden / recording not found: {ref}")
        client, project = ensure_client(settings, client, project)
        if client:
            save_settings(settings)
        new_path = move_row(settings, index, row, client, project, source)
        for c in {row.client, client} - {None}:
            build_dossier(settings, c, index.list(client=c, limit=100_000))
        return new_path
    finally:
        index.close()


def rename_speakers(settings: Settings, ref: str, mapping: dict[str, str]) -> int:
    """Rename speakers in a recording; remembered for future updates from Pocket."""
    index = Index(settings)
    try:
        row = index.find(ref)
        if not row:
            raise ValueError(f"Opname niet gevonden / recording not found: {ref}")
        path = Path(row.path)
        changed = rename_speakers_in_file(path, mapping)
        state = load_state(settings)
        stored = state.setdefault("speakers", {}).setdefault(row.pocket_id, {})
        # Map original Pocket labels (keys of the stored map) through to the new names
        reverse = {v: k for k, v in stored.items()}
        for old, new in mapping.items():
            if new and new.strip():
                stored[reverse.get(old, old)] = new.strip()
        save_state(settings, state)
        index.upsert_file(path)
        return changed
    finally:
        index.close()


def rebuild(settings: Settings) -> dict:
    """Re-scan all files and regenerate every dossier."""
    index = Index(settings)
    try:
        changed = index.refresh()
        dossiers = rebuild_all(settings, index)
        return {"changed": changed, "dossiers": dossiers}
    finally:
        index.close()


# -- Background loop ----------------------------------------------------------


class AutoSync:
    """Runs run_sync every N minutes in a daemon thread while auto_sync is on."""

    def __init__(self, on_result: Callable[[SyncResult], None] | None = None):
        self.on_result = on_result
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="pocket-autosync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(5)  # let the app start first
        while not self._stop.is_set():
            settings = load_settings()
            # Not before onboarding is done (clients first, then sorting) and never in demo mode.
            if settings.auto_sync and settings.pocket_ready and settings.onboarding.completed and not settings.demo_mode:
                try:
                    res = run_sync(settings)
                    if self.on_result:
                        self.on_result(res)
                except Exception:  # never let the loop die
                    log.exception("auto-sync failed")
            if settings.weekly_auto and settings.root.exists():
                try:
                    from . import reports

                    reports.auto_weekly(settings)
                except Exception:
                    log.exception("weekly overview failed")
            self._stop.wait(max(1, settings.sync_interval_minutes) * 60)
