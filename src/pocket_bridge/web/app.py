"""Local web app (http://127.0.0.1:8765): setup, sync, clients, recordings, action items,
reports and 'Ask Claude'."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import urllib.parse
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, ai, autostart, claude_connect, meetings, reports, semantic
from .. import sync as syncmod
from ..config import Client, config_path, default_data_dir, load_settings, save_settings
from ..index import Index
from ..pocket_api import PocketAuthError, PocketClient, PocketError
from ..storage import parse_markdown, speakers_in

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

_progress: deque[str] = deque(maxlen=200)
_sync_thread: threading.Thread | None = None
_job_thread: threading.Thread | None = None
_last_result: dict | None = None


def _say(msg: str) -> None:
    _progress.append(f"{datetime.now():%H:%M:%S}  {msg}")


def _store_result(res: syncmod.SyncResult) -> None:
    global _last_result
    if res.finished:  # skip "already running" / "no key" non-runs
        _last_result = res.as_dict()


_auto = syncmod.AutoSync(on_result=_store_result)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _auto.start()
    yield
    _auto.stop()


app = FastAPI(title="Pocket Bridge", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Only answer requests addressed to localhost (blocks DNS-rebinding tricks)."""
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and origin.split("://")[-1].split(":")[0] not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/")
def index_page():
    return FileResponse(STATIC / "index.html")


@app.get("/api/ping")
def ping():
    return {"app": "pocket-bridge", "version": __version__}


def _index():
    s = load_settings()
    idx = Index(s)
    idx.refresh()
    return s, idx


# -- State & settings -----------------------------------------------------------


def _mask(key: str) -> str:
    key = key.strip()
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else ("•••" if key else "")


@app.get("/api/state")
def get_state():
    s = load_settings()
    state = syncmod.load_state(s) if s.root.exists() else {}
    try:
        auto_on = autostart.is_enabled()
    except Exception:
        auto_on = False
    return {
        "version": __version__,
        "config_file": str(config_path()),
        "settings": {
            **s.model_dump(exclude={"pocket_api_key", "anthropic_api_key", "calendar_urls"}),
            "data_dir": str(s.root),
            "pocket_api_key_masked": _mask(s.pocket_api_key),
            "anthropic_api_key_masked": _mask(s.anthropic_api_key),
            "anthropic_from_env": bool(os.environ.get("ANTHROPIC_API_KEY")) and not s.anthropic_api_key,
            "calendar_count": len([u for u in s.calendar_urls if u.strip()]),
        },
        "pocket_ready": s.pocket_ready,
        "ai_ready": s.ai_ready,
        "autostart": auto_on,
        "claude_desktop_connected": claude_connect.is_connected(),
        "claude_desktop_config": str(claude_connect.claude_desktop_config_path()),
        "claude_code_command": claude_connect.claude_code_command(),
        "manual_snippet": claude_connect.manual_snippet(),
        "pocket_official_mcp": claude_connect.POCKET_OFFICIAL_MCP_URL,
        "last_sync": (state or {}).get("last_sync", ""),
        "last_result": _last_result or (state or {}).get("last_result"),
        "sync_running": bool(_sync_thread and _sync_thread.is_alive()),
        "job_running": bool(_job_thread and _job_thread.is_alive()),
        "progress": list(_progress)[-30:],
    }


class SettingsPatch(BaseModel):
    language: str | None = None
    pocket_api_key: str | None = None
    data_dir: str | None = None
    auto_sync: bool | None = None
    sync_interval_minutes: int | None = None
    sync_since: str | None = None
    keep_raw_json: bool | None = None
    anthropic_api_key: str | None = None
    claude_model: str | None = None
    ai_classify: bool | None = None
    ai_may_create_clients: bool | None = None
    ai_client_status: bool | None = None
    keyword_min_hits: int | None = None
    calendar_urls: list[str] | None = None
    calendar_margin_minutes: int | None = None
    weekly_auto: bool | None = None
    semantic_search: bool | None = None


@app.post("/api/settings")
def update_settings(patch: SettingsPatch):
    s = load_settings()
    data = patch.model_dump(exclude_none=True)
    nothing_synced_yet = not (s.root / ".pocket-bridge" / "state.json").exists()
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip()
        if k == "calendar_urls":
            v = [u.strip() for u in v if u.strip()]
        setattr(s, k, v)
    if "data_dir" in data and data["data_dir"]:
        s.data_dir = str(Path(data["data_dir"]).expanduser())
    if "language" in data and nothing_synced_yet:
        # Folder names follow the language chosen at setup; fixed once files exist
        s.clients_dirname = "Klanten" if s.language == "nl" else "Clients"
        s.unsorted_dirname = "_Ongesorteerd" if s.language == "nl" else "_Unsorted"
    s.sync_interval_minutes = max(1, s.sync_interval_minutes)
    save_settings(s)
    return get_state()


@app.get("/api/calendar-urls")
def calendar_urls():
    """Separate from /api/state: the links are secrets, only shown on the settings page."""
    return {"urls": load_settings().calendar_urls}


@app.post("/api/test-calendar")
def test_calendar():
    return {"results": meetings.test_urls(load_settings())}


@app.get("/api/default-folder")
def default_folder(language: str = "nl"):
    return {"path": str(default_data_dir(language))}


class KeyTest(BaseModel):
    key: str = ""


@app.post("/api/test-pocket")
def test_pocket(body: KeyTest):
    s = load_settings()
    key = body.key.strip() or s.pocket_api_key
    if not key:
        raise HTTPException(400, "no key")
    try:
        with PocketClient(key, s.pocket_base_url) as pocket:
            total = pocket.check()
    except (PocketAuthError, PocketError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "total": total}


@app.post("/api/test-anthropic")
def test_anthropic(body: KeyTest):
    s = load_settings()
    if body.key.strip():
        s.anthropic_api_key = body.key.strip()
    try:
        reply = ai.check_key(s)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "reply": reply}


class Toggle(BaseModel):
    enabled: bool


@app.post("/api/autostart")
def set_autostart(body: Toggle):
    try:
        return {"enabled": autostart.set_enabled(body.enabled)}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


# -- Clients ----------------------------------------------------------------------


@app.get("/api/clients")
def get_clients():
    s = load_settings()
    stats: dict = {}
    if s.root.exists():
        idx = Index(s)
        idx.refresh()
        stats = {row["client"]: row for row in idx.client_stats()}
        idx.close()
    out = []
    for c in s.clients:
        st = stats.get(c.name, {})
        out.append({**c.model_dump(), "recordings": st.get("recordings", 0), "last_date": st.get("last_date"), "open_actions": st.get("open_actions") or 0})
    for name, st in stats.items():
        if name and not s.find_client(name):
            out.append({"name": name, "keywords": [], "pocket_tags": [], "email_domains": [], "projects": [], "notes": "",
                        "recordings": st["recordings"], "last_date": st["last_date"], "open_actions": st["open_actions"] or 0, "folder_only": True})
    unsorted = stats.get(None, {}).get("recordings", 0)
    return {"clients": out, "unsorted": unsorted}


@app.post("/api/clients")
def upsert_client(client: Client, original_name: str = ""):
    s = load_settings()
    client.name = client.name.strip()
    if not client.name:
        raise HTTPException(400, "name required")
    client.keywords = [k.strip() for k in client.keywords if k.strip()]
    client.pocket_tags = [k.strip() for k in client.pocket_tags if k.strip()]
    client.email_domains = [k.strip().lstrip("@").lower() for k in client.email_domains if k.strip()]
    client.projects = [p for p in client.projects if p.name.strip()]
    target = s.find_client(original_name or client.name)
    if target:
        s.clients[s.clients.index(target)] = client
    else:
        s.clients.append(client)
    save_settings(s)
    return get_clients()


@app.delete("/api/clients/{name}")
def delete_client(name: str):
    """Removes the client's rules only; files stay where they are."""
    s = load_settings()
    s.clients = [c for c in s.clients if c.name.lower() != name.lower()]
    save_settings(s)
    return get_clients()


class Keywords(BaseModel):
    keywords: list[str]


@app.post("/api/clients/{name}/keywords")
def add_keywords(name: str, body: Keywords):
    reports.add_keywords(load_settings(), name, body.keywords)
    return get_clients()


@app.post("/api/clients/{name}/status")
def refresh_status(name: str):
    s, idx = _index()
    try:
        text = reports.update_client_status(s, idx, name)
        syncmod.build_dossier(s, name, idx.list(client=name, limit=100_000))
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        idx.close()
    return {"status": text}


# -- Sync -------------------------------------------------------------------------


@app.post("/api/sync")
def start_sync(full: bool = False):
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return {"started": False, "message": "already running"}
    _progress.clear()

    def work():
        _store_result(syncmod.run_sync(load_settings(), full=full, progress=_say))

    _sync_thread = threading.Thread(target=work, daemon=True)
    _sync_thread.start()
    return {"started": True}


@app.post("/api/rebuild")
def rebuild():
    return syncmod.rebuild(load_settings())


# -- Recordings -------------------------------------------------------------------


@app.get("/api/recordings")
def recordings(client: str = "", project: str = "", q: str = "", unsorted: bool = False, limit: int = 200):
    s = load_settings()
    if not s.root.exists():
        return {"recordings": []}
    idx = Index(s)
    try:
        idx.refresh()
        if q.strip():
            hits = semantic.hybrid_search(s, idx, q, client=client or None, limit=min(limit, 50))
            if project:
                hits = [h for h in hits if (h.get("project") or "").lower() == project.lower()]
            return {"recordings": hits}
        rows = idx.list(client=client or None, limit=limit, unsorted_only=unsorted, project=project or None)
        return {"recordings": [r.as_dict() for r in rows]}
    finally:
        idx.close()


@app.get("/api/recordings/{pocket_id}")
def recording(pocket_id: str):
    s = load_settings()
    idx = Index(s)
    try:
        row = idx.get(pocket_id)
        if not row:
            raise HTTPException(404, "not found")
        text = Path(row.path).read_text(encoding="utf-8")
        parsed = parse_markdown(Path(row.path))
        meta = parsed.meta if parsed else {}
        return {
            **row.as_dict(),
            "markdown": text,
            "speakers": speakers_in(text),
            "meeting": meta.get("meeting") or "",
            "attendees": meta.get("attendees") or [],
            "actions": [{"text": a, "done": d} for d, a in (parsed.action_items if parsed else [])],
        }
    finally:
        idx.close()


class Assign(BaseModel):
    client: str = ""
    project: str = ""


@app.post("/api/recordings/{pocket_id}/assign")
def assign(pocket_id: str, body: Assign):
    s = load_settings()
    try:
        path = syncmod.assign(s, pocket_id, body.client or None, body.project or None)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    suggestions: list[str] = []
    if body.client:
        s, idx = _index()
        try:
            suggestions = reports.suggest_keywords(s, idx, pocket_id, body.client)
        finally:
            idx.close()
    return {"path": str(path), "suggestions": suggestions}


class Speakers(BaseModel):
    mapping: dict[str, str]


@app.post("/api/recordings/{pocket_id}/speakers")
def rename_speakers(pocket_id: str, body: Speakers):
    try:
        n = syncmod.rename_speakers(load_settings(), pocket_id, body.mapping)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"changed": n}


@app.post("/api/recordings/{pocket_id}/speakers/guess")
def guess_speakers(pocket_id: str):
    s, idx = _index()
    try:
        return {"mapping": reports.guess_speakers(s, idx, pocket_id)}
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        idx.close()


@app.post("/api/recordings/{pocket_id}/followup")
def followup(pocket_id: str):
    s, idx = _index()
    try:
        mail = reports.make_followup(s, idx, pocket_id)
    except (ai.AIError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        idx.close()
    q = urllib.parse.urlencode({"subject": mail["subject"], "body": mail["body"]}, quote_via=urllib.parse.quote)
    mail["mailto"] = f"mailto:{','.join(mail['to'])}?{q}"
    return mail


@app.post("/api/open-folder")
def open_folder(path: str = ""):
    s = load_settings()
    target = Path(path) if path else s.root
    try:
        target.resolve().relative_to(s.root.resolve())
    except ValueError as exc:
        raise HTTPException(400, "outside data folder") from exc
    if target == s.root:
        target.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
    elif sys.platform == "win32":
        os.startfile(str(target if target.is_dir() else target.parent))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
    return {"ok": True}


# -- Action items -------------------------------------------------------------------


@app.get("/api/actions")
def actions(client: str = "", include_done: bool = False, unsorted: bool = False):
    s, idx = _index()
    try:
        return {"actions": reports.list_actions(s, idx, client or None, include_done, unsorted)}
    finally:
        idx.close()


class ActionToggle(BaseModel):
    pocket_id: str
    text: str
    done: bool


@app.post("/api/actions")
def toggle_action(body: ActionToggle):
    s, idx = _index()
    try:
        return {"changed": reports.set_action(s, idx, body.pocket_id, body.text, body.done)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        idx.close()


# -- Reports ----------------------------------------------------------------------


class BriefingReq(BaseModel):
    client: str


@app.post("/api/briefing")
def briefing(body: BriefingReq):
    s, idx = _index()
    try:
        path, text = reports.make_briefing(s, idx, body.client)
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        idx.close()
    return {"path": str(path), "markdown": text}


class WeeklyReq(BaseModel):
    week: str = ""
    ai: bool = True


@app.post("/api/weekly")
def weekly(body: WeeklyReq):
    s, idx = _index()
    try:
        path, text = reports.weekly_overview(s, idx, body.week, with_ai=body.ai)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        idx.close()
    return {"path": str(path), "markdown": text}


@app.get("/api/weekly")
def weekly_get(week: str = ""):
    s = load_settings()
    label, _, _ = reports.week_bounds(week)
    p = reports.weekly_path(s, label)
    return {"week": label, "path": str(p), "markdown": p.read_text(encoding="utf-8") if p.exists() else ""}


# -- Search on meaning --------------------------------------------------------------


@app.get("/api/semantic")
def semantic_status():
    s, idx = _index()
    try:
        return semantic.status(s, idx)
    finally:
        idx.close()


@app.post("/api/semantic/build")
def semantic_build():
    global _job_thread
    if _job_thread and _job_thread.is_alive():
        return {"started": False}
    _progress.clear()

    def work():
        s = load_settings()
        _say("Taalmodel laden (eerste keer: downloaden, ~220 MB)… / Loading model (first time: download)…")
        try:
            idx = Index(s)
            idx.refresh()
            n = semantic.update(s, idx, progress=lambda i, total: _say(f"{i}/{total}") if i % 10 == 0 or i == total else None)
            idx.close()
            _say(f"Klaar: {n} stukken tekst / Done: {n} chunks")
        except Exception as exc:
            _say(f"Mislukt / failed: {exc}")

    _job_thread = threading.Thread(target=work, daemon=True)
    _job_thread.start()
    return {"started": True}


# -- Ask Claude (streaming, with citations) ---------------------------------------------


class Ask(BaseModel):
    question: str
    client: str = ""
    history: list[dict] = []


MAX_CONTEXT_CHARS = 600_000  # ~150k tokens of transcripts per question


def _sources(s, idx: Index, question: str, client: str) -> tuple[list[dict], int]:
    if client:
        rows = idx.list(client=client, limit=200)
        hits = [h["pocket_id"] for h in semantic.hybrid_search(s, idx, question, client=client, limit=30)]
        ids = list(dict.fromkeys(hits + [r.pocket_id for r in rows]))
    else:
        hits = semantic.hybrid_search(s, idx, question, limit=12)
        ids = [h["pocket_id"] for h in hits] or [r.pocket_id for r in idx.list(limit=8)]
    sources, used, skipped = [], 0, 0
    for pid in ids:
        row = idx.get(pid)
        if not row:
            continue
        text = Path(row.path).read_text(encoding="utf-8")
        if used + len(text) > MAX_CONTEXT_CHARS:
            skipped += 1
            continue
        used += len(text)
        sources.append({"pocket_id": pid, "title": row.title, "date": (row.date or "")[:10], "client": row.client, "text": text})
    return sources, skipped


@app.post("/api/ask")
def ask(body: Ask):
    """Server-sent events: sources, text deltas, then the final blocks with citations."""
    s = load_settings()
    if not s.ai_ready:
        raise HTTPException(400, "no anthropic key")
    idx = Index(s)
    try:
        idx.refresh()
        sources, skipped = _sources(s, idx, body.question, body.client)
    finally:
        idx.close()

    def events():
        meta = [{k: v for k, v in src.items() if k != "text"} for src in sources]
        yield f"data: {json.dumps({'type': 'sources', 'sources': meta, 'skipped': skipped})}\n\n"
        if not sources:
            yield f"data: {json.dumps({'type': 'done', 'blocks': [{'text': 'Geen transcripten gevonden. / No transcripts found.', 'citations': []}]})}\n\n"
            return
        try:
            for event in ai.ask_stream(s, body.question, sources, body.history):
                yield f"data: {json.dumps(event)}\n\n"
        except ai.AIError as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# -- Claude connection ------------------------------------------------------------


@app.post("/api/connect-claude-desktop")
def connect_desktop():
    try:
        path = claude_connect.connect_claude_desktop()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(path)}
