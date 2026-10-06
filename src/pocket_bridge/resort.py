"""Re-sort recordings that are already on disk.

New recordings are sorted when they are first fetched. After clients are
added (by hand or proposed by Claude), existing recordings need the same
treatment: preview() proposes moves with a reason, apply() performs them and
writes an undo log, undo() restores the previous locations.

Recordings the user placed by hand (client_source "manual") are never touched.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from . import classify as classifier
from .config import Settings, save_settings
from .dossier import build_dossier
from .index import Index
from .pocket_api import Recording, _parse_dt
from .storage import meta_dir, parse_markdown
from .sync import ensure_client, move_row

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def recording_from_file(path: Path) -> tuple[Recording, SimpleNamespace | None] | None:
    """Rebuild enough of a Recording (and its meeting) from a Markdown file to classify it again."""
    parsed = parse_markdown(path)
    if not parsed:
        return None
    meta = parsed.meta
    tags = meta.get("tags") or []
    rec = Recording(
        id=str(meta.get("pocket_id")),
        title=parsed.title,
        recorded_at=_parse_dt(meta.get("date")),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        transcript_text=parsed.transcript,
        summary=parsed.summary,
    )
    meeting = None
    if meta.get("meeting") or meta.get("attendees"):
        attendees = [str(a) for a in (meta.get("attendees") or [])]
        emails = [m.group(0).lower() for a in attendees for m in [_EMAIL.search(a)] if m]
        meeting = SimpleNamespace(title=str(meta.get("meeting") or ""), attendees=attendees, emails=emails)
    return rec, meeting


def _source_of(path: Path) -> str:
    parsed = parse_markdown(path)
    return str(parsed.meta.get("client_source") or "") if parsed else ""


def preview(
    settings: Settings,
    index: Index,
    scope: str = "unsorted",
    hints: dict[str, str] | None = None,
    use_ai: bool = False,
) -> list[dict]:
    """Proposed moves. scope: "unsorted" or "all_auto" (everything not placed by hand).
    hints: {pocket_id: client} from a client-discovery run; these win over the rules."""
    hints = {k: v for k, v in (hints or {}).items() if v}
    rows = index.list(unsorted_only=(scope == "unsorted"), limit=100_000)
    moves = []
    for row in rows:
        path = Path(row.path)
        if scope != "unsorted" and _source_of(path) == "manual":
            continue
        rebuilt = recording_from_file(path)
        if not rebuilt:
            continue
        rec, meeting = rebuilt
        client, reason = None, ""
        if row.pocket_id in hints and settings.find_client(hints[row.pocket_id]):
            client = settings.find_client(hints[row.pocket_id]).name
            reason = "claude: voorgesteld bij het herkennen van klanten"
        else:
            client, why = classifier.by_rules(settings, rec, meeting)
            if client:
                reason = f"rule: {why}"
            elif use_ai and settings.ai_classify and settings.ai_ready:
                decision = classifier.classify(settings, rec, meeting)
                client, reason = decision.client, decision.source
        if not client:
            continue
        project = classifier.pick_project(settings, client, rec, meeting)
        if client == row.client and (project or None) == (row.project or None):
            continue
        moves.append(
            {
                "pocket_id": row.pocket_id,
                "title": row.title,
                "date": (row.date or "")[:10],
                "from_client": row.client,
                "from_project": row.project,
                "to_client": client,
                "to_project": project,
                "reason": reason,
            }
        )
    return moves


def _log_dir(settings: Settings) -> Path:
    d = meta_dir(settings) / "moves"
    d.mkdir(exist_ok=True)
    return d


def apply(settings: Settings, index: Index, moves: list[dict], extra: dict | None = None) -> dict:
    """Perform moves (each {pocket_id, to_client, to_project?, reason?}). Returns {moved, log_id}.
    extra: stored in the undo log (client discovery keeps the client list from before)."""
    undo: list[dict] = []
    touched: set[str] = set()
    for m in moves:
        row = index.get(str(m.get("pocket_id", "")))
        if not row:
            continue
        client, project = ensure_client(settings, m.get("to_client"), m.get("to_project"))
        if client == row.client and (project or None) == (row.project or None):
            continue
        undo.append({"pocket_id": row.pocket_id, "client": row.client, "project": row.project, "source": _source_of(Path(row.path))})
        move_row(settings, index, row, client, project, str(m.get("reason") or "resort"))
        touched |= {c for c in (row.client, client) if c}
    save_settings(settings)
    for c in touched:
        build_dossier(settings, c, index.list(client=c, limit=100_000))
    log_id = ""
    if undo or (extra and extra.get("created_clients")):
        stamp = datetime.now()
        log_id = stamp.strftime("%Y%m%d-%H%M%S")
        while (_log_dir(settings) / f"{log_id}.json").exists():  # two applies within one second
            stamp += timedelta(seconds=1)
            log_id = stamp.strftime("%Y%m%d-%H%M%S")
        data = {"moves": undo, **(extra or {})}
        (_log_dir(settings) / f"{log_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"moved": len(undo), "log_id": log_id}


def undo(settings: Settings, index: Index, log_id: str, return_log: bool = False):
    """Put recordings back where they were before apply(). Returns the count (or {restored, log})."""
    if not re.fullmatch(r"\d{8}-\d{6}", log_id or ""):
        raise ValueError("invalid undo id")
    path = _log_dir(settings) / f"{log_id}.json"
    if not path.exists():
        raise ValueError("nothing to undo")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("moves", [])
    touched: set[str] = set()
    restored = 0
    for e in entries:
        row = index.get(e["pocket_id"])
        if not row:
            continue
        move_row(settings, index, row, e.get("client"), e.get("project"), e.get("source") or "resort-undo")
        touched |= {c for c in (row.client, e.get("client")) if c}
        restored += 1
    for c in touched:
        build_dossier(settings, c, index.list(client=c, limit=100_000))
    path.unlink()
    if return_log:
        return {"restored": restored, "log": data if isinstance(data, dict) else {}}
    return restored
