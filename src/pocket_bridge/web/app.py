"""Local web app (http://127.0.0.1:8765): setup wizard, sync, clients, recordings and 'Ask Claude'."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, ai, claude_connect
from .. import sync as syncmod
from ..config import Client, Settings, config_path, default_data_dir, load_settings, save_settings
from ..index import Index
from ..pocket_api import PocketAuthError, PocketClient, PocketError

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _auto.start()
    yield
    _auto.stop()


app = FastAPI(title="Pocket Bridge", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_progress: deque[str] = deque(maxlen=200)
_sync_thread: threading.Thread | None = None
_last_result: dict | None = None


def _say(msg: str) -> None:
    _progress.append(f"{datetime.now():%H:%M:%S}  {msg}")


def _store_result(res: syncmod.SyncResult) -> None:
    global _last_result
    if res.finished:  # skip "already running" / "no key" non-runs
        _last_result = res.as_dict()


_auto = syncmod.AutoSync(on_result=_store_result)


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


# -- State & settings -----------------------------------------------------------


def _mask(key: str) -> str:
    key = key.strip()
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else ("•••" if key else "")


@app.get("/api/state")
def get_state():
    s = load_settings()
    state = syncmod.load_state(s) if s.root.exists() else {}
    return {
        "version": __version__,
        "config_file": str(config_path()),
        "settings": {
            **s.model_dump(exclude={"pocket_api_key", "anthropic_api_key"}),
            "data_dir": str(s.root),
            "pocket_api_key_masked": _mask(s.pocket_api_key),
            "anthropic_api_key_masked": _mask(s.anthropic_api_key),
            "anthropic_from_env": bool(os.environ.get("ANTHROPIC_API_KEY")) and not s.anthropic_api_key,
        },
        "pocket_ready": s.pocket_ready,
        "ai_ready": s.ai_ready,
        "claude_desktop_connected": claude_connect.is_connected(),
        "claude_desktop_config": str(claude_connect.claude_desktop_config_path()),
        "claude_code_command": claude_connect.claude_code_command(),
        "manual_snippet": claude_connect.manual_snippet(),
        "pocket_official_mcp": claude_connect.POCKET_OFFICIAL_MCP_URL,
        "last_sync": (state or {}).get("last_sync", ""),
        "last_result": _last_result or (state or {}).get("last_result"),
        "sync_running": bool(_sync_thread and _sync_thread.is_alive()),
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
    keyword_min_hits: int | None = None


@app.post("/api/settings")
def update_settings(patch: SettingsPatch):
    s = load_settings()
    data = patch.model_dump(exclude_none=True)
    nothing_synced_yet = not (s.root / ".pocket-bridge" / "state.json").exists()
    for k, v in data.items():
        setattr(s, k, v.strip() if isinstance(v, str) else v)
    if "data_dir" in data and data["data_dir"]:
        s.data_dir = str(Path(data["data_dir"]).expanduser())
    if "language" in data and nothing_synced_yet:
        # Folder names follow the language chosen at setup; fixed once files exist
        s.clients_dirname = "Klanten" if s.language == "nl" else "Clients"
        s.unsorted_dirname = "_Ongesorteerd" if s.language == "nl" else "_Unsorted"
    s.sync_interval_minutes = max(1, s.sync_interval_minutes)
    save_settings(s)
    return get_state()


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
    except PocketAuthError as exc:
        return {"ok": False, "error": str(exc)}
    except PocketError as exc:
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
            out.append({"name": name, "keywords": [], "pocket_tags": [], "notes": "", "recordings": st["recordings"], "last_date": st["last_date"], "open_actions": st["open_actions"] or 0, "folder_only": True})
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
    target = s.find_client(original_name or client.name)
    if target:
        idx = s.clients.index(target)
        s.clients[idx] = client
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
def recordings(client: str = "", q: str = "", unsorted: bool = False, limit: int = 200):
    s = load_settings()
    if not s.root.exists():
        return {"recordings": []}
    idx = Index(s)
    try:
        idx.refresh()
        if q.strip():
            hits = idx.search(q, client=client or None, limit=limit) or idx.search(q, client=client or None, limit=limit, any_word=True)
            return {"recordings": hits}
        rows = idx.list(client=client or None, limit=limit, unsorted_only=unsorted)
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
        return {**row.as_dict(), "markdown": Path(row.path).read_text(encoding="utf-8")}
    finally:
        idx.close()


class Assign(BaseModel):
    client: str = ""


@app.post("/api/recordings/{pocket_id}/assign")
def assign(pocket_id: str, body: Assign):
    try:
        path = syncmod.assign(load_settings(), pocket_id, body.client or None)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"path": str(path)}


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


# -- Ask Claude -------------------------------------------------------------------


class Ask(BaseModel):
    question: str
    client: str = ""
    history: list[dict] = []


MAX_CONTEXT_CHARS = 600_000  # ~150k tokens of transcripts per question


@app.post("/api/ask")
def ask(body: Ask):
    s = load_settings()
    if not s.ai_ready:
        raise HTTPException(400, "no anthropic key")
    idx = Index(s)
    try:
        idx.refresh()
        if body.client:
            rows = idx.list(client=body.client, limit=200)
            ids = [r.pocket_id for r in rows]
            # Put the conversations that match the question first
            hits = [h["pocket_id"] for h in idx.search(body.question, client=body.client, limit=50, any_word=True)]
            ids = list(dict.fromkeys(hits + ids))
        else:
            hits = idx.search(body.question, limit=12, any_word=True)
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
    finally:
        idx.close()
    if not sources:
        return {"answer": "Geen transcripten gevonden om te doorzoeken. / No transcripts to search.", "sources": [], "skipped": 0}
    try:
        answer = ai.ask(s, body.question, sources, body.history)
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "answer": answer,
        "sources": [{k: v for k, v in src.items() if k != "text"} for src in sources],
        "skipped": skipped,
    }


# -- Claude connection ------------------------------------------------------------


@app.post("/api/connect-claude-desktop")
def connect_desktop():
    try:
        path = claude_connect.connect_claude_desktop()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(path)}


def create_settings_if_missing() -> Settings:
    s = load_settings()
    if not config_path().exists():
        save_settings(s)
    return s
