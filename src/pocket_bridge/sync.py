"""Pull recordings from Pocket and write them to disk, sorted per client."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx

from . import classify as classifier
from .config import Client, Settings, load_settings, save_settings
from .dossier import build_dossier, rebuild_all
from .i18n import t
from .index import Index
from .pocket_api import PocketClient, Recording
from .storage import client_dir, meta_dir, render_markdown, set_frontmatter_client, target_path, unique_path

log = logging.getLogger(__name__)
_thread_lock = threading.Lock()


@dataclass
class SyncResult:
    started: str = ""
    finished: str = ""
    new: int = 0
    updated: int = 0
    skipped: int = 0
    pending: int = 0  # still processing in Pocket; retried next time
    errors: list[str] = field(default_factory=list)
    new_clients: list[str] = field(default_factory=list)
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
) -> SyncResult:
    settings = settings or load_settings()
    say = progress or (lambda msg: log.info(msg))
    result = SyncResult(started=datetime.now().isoformat(timespec="seconds"))
    if not settings.pocket_ready:
        result.message = t(settings.language, "sync_no_key")
        return result

    if not _thread_lock.acquire(blocking=False):
        result.message = t(settings.language, "sync_busy")
        return result
    lock = FileLock(meta_dir(settings) / "sync.lock")
    if not lock.acquire():
        _thread_lock.release()
        result.message = t(settings.language, "sync_busy")
        return result
    try:
        _do_sync(settings, full, say, result, transport)
    finally:
        lock.release()
        _thread_lock.release()
    result.finished = datetime.now().isoformat(timespec="seconds")
    return result


def _do_sync(settings: Settings, full: bool, say, result: SyncResult, transport) -> None:
    state = load_state(settings)
    known: dict = state.setdefault("recordings", {})
    index = Index(settings)
    index.refresh()  # pick up files the user moved by hand
    touched_clients: set[str] = set()

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
                if not rid:
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
                if not rec.has_transcript:
                    result.pending += 1
                    continue
                say(f"[{n}/{len(items)}] {rec.title}")
                if settings.keep_raw_json:
                    raw_dir.mkdir(exist_ok=True)
                    (raw_dir / f"{rid}.json").write_text(json.dumps(rec.raw, indent=1, ensure_ascii=False), encoding="utf-8")

                if existing:
                    path = Path(existing.path)
                    client = existing.client
                    source = _current_source(path) or "kept"
                    result.updated += 1
                else:
                    client, source = classifier.classify(settings, rec)
                    if client and not settings.find_client(client):
                        settings.clients.append(Client(name=client))
                        save_settings(settings)
                        result.new_clients.append(client)
                    path = unique_path(target_path(settings, rec, client))
                    result.new += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(render_markdown(settings, rec, client, source), encoding="utf-8")
                index.upsert_file(path)
                known[rid] = {"updated_at": updated or rec.updated_at, "synced_at": datetime.now().isoformat(timespec="seconds")}
                if client:
                    touched_clients.add(client)
                if n % 10 == 0:
                    save_state(settings, state)
    except Exception as exc:
        result.errors.append(str(exc))
        result.message = str(exc)

    for client in touched_clients:
        build_dossier(settings, client, index.list(client=client, limit=100_000))
    state["last_sync"] = result.started if not result.message else state.get("last_sync", "")
    state["last_result"] = result.as_dict()
    save_state(settings, state)
    index.close()
    if not result.message:
        result.message = t(settings.language, "sync_done", new=result.new, updated=result.updated, pending=result.pending)
    say(result.message)


def _current_source(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:20]:
            if line.startswith("client_source:"):
                return json.loads(line.split(":", 1)[1])
    except Exception:
        pass
    return ""


# -- Manual re-assignment -----------------------------------------------------


def assign(settings: Settings, ref: str, client: str | None) -> Path:
    """Move a recording to another client's folder (or to Unsorted with client=None)."""
    index = Index(settings)
    try:
        index.refresh()
        row = index.find(ref)
        if not row:
            raise ValueError(f"Opname niet gevonden / recording not found: {ref}")
        client = client.strip() if client else None
        if client:
            known = settings.find_client(client)
            if known:
                client = known.name
            else:
                settings.clients.append(Client(name=client))
                save_settings(settings)
        old_path = Path(row.path)
        year = old_path.parent.name
        new_path = unique_path(client_dir(settings, client) / year / old_path.name) if old_path.parent != client_dir(settings, client) / year else old_path
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if new_path != old_path:
            shutil.move(str(old_path), str(new_path))
        set_frontmatter_client(new_path, client, "manual")
        index.upsert_file(new_path)
        for c in {row.client, client} - {None}:
            build_dossier(settings, c, index.list(client=c, limit=100_000))
        return new_path
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
            if settings.auto_sync and settings.pocket_ready:
                try:
                    res = run_sync(settings)
                    if self.on_result:
                        self.on_result(res)
                except Exception:  # never let the loop die
                    log.exception("auto-sync failed")
            self._stop.wait(max(1, settings.sync_interval_minutes) * 60)
