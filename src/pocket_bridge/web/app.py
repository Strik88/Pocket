"""Local web app (http://127.0.0.1:8765): setup, sync, clients, recordings, action items,
reports and 'Ask Claude'."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, ai, autostart, claude_connect, demo, discovery, meetings, reports, semantic
from .. import resort as resorter
from .. import sync as syncmod
from ..dossier import read_status
from ..config import Client, Onboarding, config_path, default_data_dir, load_settings, save_settings
from ..index import Index
from ..pocket_api import PocketAuthError, PocketClient, PocketError
from . import session
from ..storage import parse_markdown, speakers_in

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# Windows can map .js to text/plain in the registry; browsers refuse ES modules served like that.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")

_progress: deque[str] = deque(maxlen=200)
_sync_thread: threading.Thread | None = None
_job_thread: threading.Thread | None = None
_last_result: dict | None = None
_sync_count = {"done": 0, "total": 0, "title": ""}
_discover = {"running": False, "done": 0, "total": 0, "error": "", "code": ""}
_STEP = re.compile(r"^\[(\d+)/(\d+)\] (.*)$")


def _say(msg: str) -> None:
    m = _STEP.match(msg)
    if m:
        _sync_count.update(done=int(m.group(1)), total=int(m.group(2)), title=m.group(3))
    _progress.append(f"{datetime.now():%H:%M:%S}  {msg}")


def _err(status: int, code: str, message: str = "") -> HTTPException:
    """Errors carry a stable code that the frontend translates."""
    return HTTPException(status, {"code": code, "message": message})


def _ai_err(exc: Exception) -> HTTPException:
    return _err(502, getattr(exc, "code", "claude_error"), str(exc))


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


_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}

LOCKED_PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pocket Bridge by Striks</title><link rel="icon" type="image/png" href="/static/img/striks-icon-color.png">
<style>body{font-family:system-ui,sans-serif;background:#F7F9FC;color:#1A2B50;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}
main{background:#fff;border:1px solid #E3E8F0;border-radius:16px;padding:32px;max-width:520px}h1{font-size:1.5rem}p{line-height:1.6;color:#4A5878}</style></head>
<body><main><img src="/static/img/striks-icon-color.png" alt="" width="48"><h1>Open Pocket Bridge via het icoon</h1>
<p>Om je gesprekken te beschermen opent Pocket Bridge alleen in de browser die het programma zelf opent. Klik op het Striks-strikje in de menubalk (Mac) of bij de klok (Windows) en kies <b>Open Pocket Bridge</b>, of start het programma opnieuw.</p>
<p lang="en">To protect your conversations, Pocket Bridge only opens in the browser the program opens itself. Use <b>Open Pocket Bridge</b> in the tray icon menu, or start the program again.</p></main></body></html>"""


def _authorised(request: Request) -> bool:
    return session.valid(request.cookies.get(session.COOKIE)) or session.valid(request.headers.get("x-pocket-bridge-token"))


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Only answer requests addressed to this app on localhost, from its own pages or its own tray icon."""
    host_header = request.headers.get("host") or ""
    host, _, port = host_header.partition(":")
    if host not in ("127.0.0.1", "localhost"):  # blocks DNS rebinding
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    path = request.url.path
    if path.startswith("/api/") and path != "/api/ping":
        if not _authorised(request):
            return JSONResponse({"detail": {"code": "auth"}}, status_code=401)
        if request.method != "GET":
            own = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"} if port else {"http://127.0.0.1", "http://localhost"}
            origin = request.headers.get("origin")
            if (origin and origin not in own) or request.headers.get("x-pocket-bridge") != "1":
                return JSONResponse({"detail": {"code": "forbidden"}}, status_code=403)
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index_page(request: Request):
    if not _authorised(request):
        return HTMLResponse(LOCKED_PAGE, status_code=401)
    return FileResponse(STATIC / "index.html")


@app.get("/auth")
def auth(t: str = ""):
    """The app opens the browser here; the key becomes a cookie and disappears from the address bar."""
    if not session.valid(t):
        return HTMLResponse(LOCKED_PAGE, status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(session.COOKIE, session.token(), max_age=400 * 24 * 3600, httponly=True, samesite="strict", path="/")
    return resp


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
    unsorted = 0
    memory = discovery.load_memory(s) if s.root.exists() else {}
    if s.root.exists():
        idx = Index(s)
        try:
            idx.refresh()
            ignored = set(memory.get("ignored_recordings") or {})
            unsorted = sum(1 for r in idx.list(unsorted_only=True, limit=100_000) if r.pocket_id not in ignored)
        finally:
            idx.close()
    proposal = discovery.load_proposal(s) if s.root.exists() else None
    return {
        "version": __version__,
        "config_file": str(config_path()),
        "unsorted": unsorted,
        "suggestions": list((memory.get("suggestions") or {}).values()),
        "proposal": {"id": proposal["id"], "clients": len(proposal["clients"]), "source": proposal["source"]} if proposal else None,
        "discover": dict(_discover),
        "sync_count": dict(_sync_count),
        "demo_questions": demo.questions() if s.demo_mode else [],
        "demo_total": demo.total() if s.demo_mode else 0,
        "claude_desktop_installed": claude_connect.desktop_installed(),
        "claude_desktop_seen": claude_connect.last_seen(),
        "settings": {
            **s.model_dump(exclude={"pocket_api_key", "anthropic_api_key", "calendar_urls", "clients"}),
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
    user_name: str | None = None
    own_domains: list[str] | None = None
    pocket_api_key: str | None = None
    data_dir: str | None = None
    auto_sync: bool | None = None
    sync_interval_minutes: int | None = None
    sync_since: str | None = None
    keep_raw_json: bool | None = None
    anthropic_api_key: str | None = None
    claude_model: str | None = None
    ai_classify: bool | None = None
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
        if k == "own_domains":
            v = [d.strip().lower().lstrip("@") for d in v if d.strip()]
        if k == "language" and v not in ("nl", "en"):
            continue
        if k == "claude_model" and v not in ai.MODEL_PRICES:
            raise _err(400, "bad_model")
        if k == "sync_since" and v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise _err(400, "bad_date")
        setattr(s, k, v)
    if "data_dir" in data and data["data_dir"]:
        s.data_dir = str(Path(data["data_dir"]).expanduser())
    if "language" in data and nothing_synced_yet:
        # Folder names follow the language chosen at setup; fixed once files exist
        s.clients_dirname = "Klanten" if s.language == "nl" else "Clients"
        s.unsorted_dirname = "_Ongesorteerd" if s.language == "nl" else "_Unsorted"
    s.sync_interval_minutes = max(1, s.sync_interval_minutes)
    s.keyword_min_hits = min(10, max(1, s.keyword_min_hits))
    save_settings(s)
    if "calendar_urls" in data:
        meetings.prune_cache(s)
    return get_state()


class OnboardingPatch(BaseModel):
    step: str | None = None
    completed: bool | None = None
    claude_mode: str | None = None
    skip: str | None = None


ONBOARDING_STEPS = ("welcome", "pocket", "claude", "fetch", "discover", "sort", "desktop", "extras", "done")


@app.post("/api/onboarding")
def update_onboarding(patch: OnboardingPatch):
    s = load_settings()
    ob = s.onboarding
    if patch.step is not None:
        if patch.step not in ONBOARDING_STEPS:
            raise _err(400, "bad_step")
        ob.step = patch.step
    if patch.claude_mode is not None and patch.claude_mode in ("api", "desktop", "none", ""):
        ob.claude_mode = patch.claude_mode
    if patch.skip and patch.skip in ONBOARDING_STEPS and patch.skip not in ob.skipped:
        ob.skipped.append(patch.skip)
    if patch.completed is not None:
        ob.completed = patch.completed
        if patch.completed:
            ob.step = "done"
    save_settings(s)
    return get_state()


@app.post("/api/onboarding/restart")
def restart_onboarding():
    s = load_settings()
    s.onboarding = Onboarding(step="welcome")
    save_settings(s)
    return get_state()


# -- Folders -------------------------------------------------------------------------


def _quick_folders(language: str) -> list[dict]:
    home = Path.home()
    name = "Pocket Transcripten" if language == "nl" else "Pocket Transcripts"
    cands = [("documents", home / "Documents"), ("icloud", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")]
    cands += [("onedrive", p) for p in sorted(home.glob("OneDrive*"))[:2]]
    cands += [("dropbox", home / "Dropbox")]
    cands += [("gdrive", p) for p in sorted((home / "Library" / "CloudStorage").glob("GoogleDrive-*"))[:1]]
    cands += [("gdrive", home / "Google Drive"), ("gdrive", Path("G:/My Drive")), ("gdrive", Path("G:/Mijn Drive"))]
    out, seen = [], set()
    for kind, p in cands:
        try:
            if p.is_dir() and kind not in seen:
                seen.add(kind)
                out.append({"kind": kind, "path": str(p / name)})
        except OSError:
            continue
    return out


@app.get("/api/folders")
def folders():
    s = load_settings()
    return {"current": str(s.root), "default": str(default_data_dir(s.language)), "quick": _quick_folders(s.language)}


@app.post("/api/folders/pick")
def pick_folder():
    """Open the operating system's folder picker (the app runs on the user's own computer)."""
    s = load_settings()
    title = "Kies een map voor je gesprekken" if s.language == "nl" else "Choose a folder for your conversations"
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["osascript", "-e", f'POSIX path of (choose folder with prompt "{title}")'],
                               capture_output=True, text=True, timeout=600)
            path = r.stdout.strip()
        elif sys.platform == "win32":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  f"$d.Description='{title}';$d.ShowNewFolderButton=$true;"
                  "if($d.ShowDialog() -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            path = r.stdout.strip()
        elif shutil.which("zenity"):
            r = subprocess.run(["zenity", "--file-selection", "--directory", f"--title={title}"], capture_output=True, text=True, timeout=600)
            path = r.stdout.strip()
        else:
            return {"supported": False}
    except (OSError, subprocess.SubprocessError):
        return {"supported": False}
    return {"supported": True, "path": path, "cancelled": not path}


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
    """Test a key and, when it works, save it (so the user never has to press a separate save)."""
    s = load_settings()
    key = body.key.strip() or s.pocket_api_key
    if not key:
        raise _err(400, "pocket_no_key")
    try:
        with PocketClient(key, s.pocket_base_url) as pocket:
            total = pocket.check()
    except PocketAuthError as exc:
        return {"ok": False, "code": "pocket_key_invalid", "error": str(exc)}
    except PocketError as exc:
        return {"ok": False, "code": "pocket_error", "error": str(exc)}
    if body.key.strip() and body.key.strip() != s.pocket_api_key:
        s.pocket_api_key = body.key.strip()
        save_settings(s)
    return {"ok": True, "total": total}


@app.post("/api/test-anthropic")
def test_anthropic(body: KeyTest):
    """Test a key with a tiny request and save it when it works."""
    s = load_settings()
    if body.key.strip():
        s.anthropic_api_key = body.key.strip()
    try:
        reply = ai.check_key(s)
    except ai.AIError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "code": "claude_error", "error": str(exc)}
    if body.key.strip():
        saved = load_settings()
        saved.anthropic_api_key = body.key.strip()
        saved.onboarding.claude_mode = saved.onboarding.claude_mode or "api"
        save_settings(saved)
    return {"ok": True, "reply": reply}


@app.post("/api/forget-key")
def forget_key(body: KeyTest):
    """body.key: "pocket" or "anthropic"."""
    s = load_settings()
    if body.key == "pocket":
        s.pocket_api_key = ""
    elif body.key == "anthropic":
        s.anthropic_api_key = ""
    save_settings(s)
    return get_state()


class Toggle(BaseModel):
    enabled: bool


@app.post("/api/autostart")
def set_autostart(body: Toggle):
    try:
        return {"enabled": autostart.set_enabled(body.enabled)}
    except Exception as exc:
        raise _err(500, "autostart_failed", str(exc)) from exc


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
        status, updated = read_status(s, c.name) if s.root.exists() else ("", "")
        out.append({**c.model_dump(), "recordings": st.get("recordings", 0), "last_date": st.get("last_date"),
                    "open_actions": st.get("open_actions") or 0, "status": status, "status_updated": updated})
    for name, st in stats.items():
        if name and not s.find_client(name):
            out.append({"name": name, "keywords": [], "pocket_tags": [], "email_domains": [], "projects": [], "notes": "",
                        "recordings": st["recordings"], "last_date": st["last_date"], "open_actions": st["open_actions"] or 0, "folder_only": True})
    ignored = len(discovery.load_memory(s).get("ignored_recordings") or {}) if s.root.exists() else 0
    unsorted = max(0, stats.get(None, {}).get("recordings", 0) - ignored)
    return {"clients": out, "unsorted": unsorted}


@app.post("/api/clients")
def upsert_client(client: Client, original_name: str = ""):
    s = load_settings()
    name = discovery.clean_name(s, client.name, strip_legal=False)
    if not name:
        raise _err(400, "bad_name")
    client.name = name
    client.keywords = [k.strip() for k in client.keywords if k.strip()]
    client.pocket_tags = [k.strip() for k in client.pocket_tags if k.strip()]
    client.email_domains = [k.strip().lstrip("@").lower() for k in client.email_domains if k.strip()]
    projects = []
    for p in client.projects:
        pname = discovery.clean_name(s, p.name, strip_legal=False)
        if pname and pname.lower() not in {x.name.lower() for x in projects}:
            p.name = pname
            p.keywords = [k.strip()[:40] for k in p.keywords if k.strip()][:20]
            projects.append(p)
    client.projects = projects
    client.email_domains = [d for d in client.email_domains if discovery.valid_domain(d)]
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
    removed = s.find_client(name)
    s.clients = [c for c in s.clients if c.name.lower() != name.lower()]
    save_settings(s)
    return {**get_clients(), "removed": removed.model_dump() if removed else None}


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
        raise _ai_err(exc) from exc
    finally:
        idx.close()
    return {"status": text}


@app.get("/api/stats")
def stats():
    """Numbers for the overview page."""
    s = load_settings()
    if not s.root.exists():
        return {"recordings": 0, "last7": 0, "last7_clients": 0, "last30": 0, "clients": len(s.clients), "open_actions": 0, "minutes": 0, "unsorted": 0}
    idx = Index(s)
    try:
        idx.refresh()
        rows = idx.list(limit=100_000)
    finally:
        idx.close()
    today = datetime.now().date()
    d7, d30 = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=30)).isoformat()
    last7 = [r for r in rows if r.date and r.date[:10] >= d7]
    return {
        "recordings": len(rows),
        "last7": len(last7),
        "last7_clients": len({r.client for r in last7 if r.client}),
        "last30": sum(1 for r in rows if r.date and r.date[:10] >= d30),
        "clients": len(s.clients),
        "open_actions": sum(r.open_actions or 0 for r in rows),
        "minutes": sum(r.duration_minutes or 0 for r in rows),
        "unsorted": sum(1 for r in rows if not r.client),
    }


# -- Sync -------------------------------------------------------------------------


@app.post("/api/sync")
def start_sync(full: bool = False):
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return {"started": False, "message": "already running"}
    _progress.clear()
    _sync_count.update(done=0, total=0, title="")
    settings = load_settings()
    # During onboarding there are no clients yet: fetch only, sorting comes after the clients are known.
    allow_ai = settings.onboarding.completed

    def work():
        _store_result(syncmod.run_sync(load_settings(), full=full, progress=_say, allow_ai=allow_ai))

    _sync_thread = threading.Thread(target=work, daemon=True)
    _sync_thread.start()
    return {"started": True}


@app.post("/api/rebuild")
def rebuild():
    return syncmod.rebuild(load_settings())


# -- Recordings -------------------------------------------------------------------


@app.get("/api/recordings")
def recordings(client: str = "", project: str = "", q: str = "", unsorted: bool = False, limit: int = 200, skip_ignored: bool = False):
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
        if skip_ignored:  # private notes and internal meetings the user chose to leave without a client
            ignored = set(discovery.load_memory(s).get("ignored_recordings") or {})
            kept = [r for r in rows if r.pocket_id not in ignored]
            return {"recordings": [r.as_dict() for r in kept], "ignored": len(rows) - len(kept)}
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
            raise _err(404, "not_found")
        text = Path(row.path).read_text(encoding="utf-8")
        parsed = parse_markdown(Path(row.path))
        meta = parsed.meta if parsed else {}
        return {
            **row.as_dict(),
            "markdown": text,
            "summary": parsed.summary if parsed else "",
            "transcript": parsed.transcript if parsed else "",
            "client_source": str(meta.get("client_source") or ""),
            "language": str(meta.get("language") or ""),
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
        raise _err(400, "bad_name" if "invalid" in str(exc) else "not_found", str(exc)) from exc
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
        n = syncmod.rename_speakers(load_settings(), pocket_id, {k: v[:80] for k, v in body.mapping.items()})
    except ValueError as exc:
        raise _err(404, "not_found", str(exc)) from exc
    return {"changed": n}


@app.post("/api/recordings/{pocket_id}/speakers/guess")
def guess_speakers(pocket_id: str):
    s, idx = _index()
    try:
        return {"mapping": reports.guess_speakers(s, idx, pocket_id)}
    except ai.AIError as exc:
        raise _ai_err(exc) from exc
    finally:
        idx.close()


@app.post("/api/recordings/{pocket_id}/followup")
def followup(pocket_id: str):
    s, idx = _index()
    try:
        if s.demo_mode and not s.ai_ready and demo.followup(pocket_id):
            mail = {**demo.followup(pocket_id), "to": [], "example": True}
            row = idx.get(pocket_id)
            parsed = parse_markdown(Path(row.path)) if row else None
            mail["to"] = [a for a in ((parsed.meta.get("attendees") if parsed else None) or []) if "koersadvies" not in a]
        else:
            mail = reports.make_followup(s, idx, pocket_id)
    except ai.AIError as exc:
        raise _ai_err(exc) from exc
    except ValueError as exc:
        raise _err(400, "not_found", str(exc)) from exc
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
        raise _err(400, "outside_folder") from exc
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
        raise _err(404, "not_found", str(exc)) from exc
    finally:
        idx.close()


# -- Reports ----------------------------------------------------------------------


class BriefingReq(BaseModel):
    client: str


@app.post("/api/briefing")
def briefing(body: BriefingReq):
    s, idx = _index()
    try:
        if s.demo_mode and not s.ai_ready and demo.briefing(body.client):
            return {"path": "", "markdown": demo.briefing(body.client), "example": True}
        path, text = reports.make_briefing(s, idx, body.client)
    except ai.AIError as exc:
        raise _ai_err(exc) from exc
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
        path, text = reports.weekly_overview(s, idx, body.week, with_ai=body.ai and s.ai_ready)
    except ValueError as exc:
        raise _err(400, "bad_week", str(exc)) from exc
    except ai.AIError as exc:
        raise _ai_err(exc) from exc
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
        _say("model_loading")
        try:
            idx = Index(s)
            idx.refresh()
            n = semantic.update(s, idx, progress=lambda i, total: _say(f"{i}/{total}") if i % 10 == 0 or i == total else None)
            idx.close()
            _say(f"model_done {n}")
        except Exception as exc:
            _say(f"model_failed {exc}")

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


def _demo_answer(s, question: str):
    """Pre-written answer to one of the example questions (demo mode without a Claude key)."""
    blocks = demo.answer(question)
    if blocks is None:
        return None
    idx = Index(s)
    try:
        idx.refresh()
        order: list[str] = []
        for b in blocks:
            for c in b.get("citations", []):
                if c["pocket_id"] not in order:
                    order.append(c["pocket_id"])
        sources = []
        for pid in order:
            row = idx.get(pid)
            if row:
                sources.append({"pocket_id": pid, "title": row.title, "date": (row.date or "")[:10], "client": row.client})
    finally:
        idx.close()
    pos = {src["pocket_id"]: i for i, src in enumerate(sources)}
    out = [{"text": b["text"], "citations": [{"source": pos[c["pocket_id"]], "cited_text": c["cited_text"]}
                                            for c in b.get("citations", []) if c["pocket_id"] in pos]} for b in blocks]
    return sources, out


@app.post("/api/ask")
def ask(body: Ask):
    """Server-sent events: sources, text deltas, then the final blocks with citations."""
    s = load_settings()
    if not s.ai_ready:
        prepared = _demo_answer(s, body.question) if s.demo_mode else None
        if not prepared:
            raise _err(400, "claude_no_key_demo" if s.demo_mode else "claude_no_key")
        sources, blocks = prepared

        def demo_events():
            import time

            yield f"data: {json.dumps({'type': 'sources', 'sources': sources, 'skipped': 0, 'example': True})}\n\n"
            for b in blocks:
                for word in re.findall(r"\S+\s*", b["text"]):
                    time.sleep(0.02)
                    yield f"data: {json.dumps({'type': 'text', 'text': word})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'blocks': blocks, 'example': True})}\n\n"

        return StreamingResponse(demo_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    idx = Index(s)
    try:
        idx.refresh()
        sources, skipped = _sources(s, idx, body.question, body.client)
    finally:
        idx.close()

    history = [{"role": h.get("role"), "content": str(h.get("content", ""))[:20_000]}
               for h in body.history[-12:] if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str)]

    def events():
        meta = [{k: v for k, v in src.items() if k != "text"} for src in sources]
        yield f"data: {json.dumps({'type': 'sources', 'sources': meta, 'skipped': skipped})}\n\n"
        if not sources:
            yield f"data: {json.dumps({'type': 'error', 'code': 'no_sources'})}\n\n"
            return
        try:
            for event in ai.ask_stream(s, body.question[:4000], sources, history):
                yield f"data: {json.dumps(event)}\n\n"
        except ai.AIError as exc:
            yield f"data: {json.dumps({'type': 'error', 'code': exc.code, 'error': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# -- Claude connection ------------------------------------------------------------


@app.post("/api/connect-claude-desktop")
def connect_desktop():
    try:
        path = claude_connect.connect_claude_desktop()
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "code": "desktop_config", "error": str(exc)}
    s = load_settings()
    if s.onboarding.claude_mode in ("", "none"):
        s.onboarding.claude_mode = "desktop" if not s.ai_ready else s.onboarding.claude_mode or "api"
        save_settings(s)
    return {"ok": True, "path": str(path)}


# -- Client discovery ("Claude stelt je klanten voor") -------------------------------------


@app.get("/api/discover/estimate")
def discover_estimate():
    s, idx = _index()
    try:
        digests, info = discovery.build_digests(s, idx)
    finally:
        idx.close()
    est = discovery.estimate(s, digests)
    return {**est, "owner": info["owner"], "own_domains": info["own_domains"], "ai_ready": s.ai_ready,
            "demo_example": s.demo_mode and not s.ai_ready}


class DiscoverReq(BaseModel):
    route: str = "claude"  # "claude" | "rules" | "example"
    user_name: str | None = None
    own_domains: list[str] | None = None


@app.post("/api/discover")
def discover(body: DiscoverReq):
    global _job_thread
    if _discover["running"]:
        return {"started": False}
    s = load_settings()
    if body.user_name is not None:
        s.user_name = body.user_name.strip()[:80]
    if body.own_domains is not None:
        s.own_domains = [d.strip().lower().lstrip("@") for d in body.own_domains if d.strip()][:5]
    save_settings(s)
    route = body.route
    if route == "claude" and not s.ai_ready:
        route = "example" if s.demo_mode else "rules"
    if route == "example" and not s.demo_mode:
        route = "rules"
    _discover.update(running=True, done=0, total=0, error="", code="")

    def work():
        st = load_settings()
        idx = Index(st)
        try:
            idx.refresh()
            if route == "claude":
                discovery.run(st, idx, progress=lambda d, t: _discover.update(done=d, total=t))
            elif route == "example":
                digests, info = discovery.build_digests(st, idx)
                ids = {d["pocket_id"]: d["pocket_id"] for d in digests}
                prop = discovery.validate(st, idx, demo.raw_proposal(), ids, "example", info, digests)
                discovery.save_proposal(st, prop)
            else:
                discovery.heuristic(st, idx)
        except ai.AIError as exc:
            _discover.update(error=str(exc), code=exc.code)
        except ValueError as exc:
            _discover.update(error=str(exc), code="no_recordings")
        except Exception as exc:  # shown in the app instead of a dead spinner
            log.exception("discovery failed")
            _discover.update(error=str(exc), code="discover_failed")
        finally:
            idx.close()
            _discover["running"] = False

    _job_thread = threading.Thread(target=work, daemon=True)
    _job_thread.start()
    return {"started": True, "route": route}


@app.get("/api/discover/proposal")
def discover_proposal():
    s = load_settings()
    return {"proposal": discovery.load_proposal(s) if s.root.exists() else None, "status": dict(_discover)}


class ApplyReq(BaseModel):
    clients: list[dict]
    ignore: dict[str, str] = {}


@app.post("/api/discover/apply")
def discover_apply(body: ApplyReq):
    s, idx = _index()
    try:
        result = discovery.apply(s, idx, body.clients, body.ignore)
        if s.demo_mode:
            demo.write_statuses(load_settings(), idx, result["created"] + result["updated"])
    finally:
        idx.close()
    return result


@app.post("/api/discover/dismiss")
def discover_dismiss():
    discovery.dismiss_proposal(load_settings())
    return {"ok": True}


class UndoReq(BaseModel):
    undo_id: str


@app.post("/api/discover/undo")
def discover_undo(body: UndoReq):
    s, idx = _index()
    try:
        return discovery.undo(s, idx, body.undo_id)
    except ValueError as exc:
        raise _err(400, "undo_failed", str(exc)) from exc
    finally:
        idx.close()


class SuggestionReq(BaseModel):
    name: str
    accept: bool = True


@app.post("/api/suggestions")
def handle_suggestion(body: SuggestionReq):
    """Accept or reject a possible new client that Claude noticed during a sync."""
    s, idx = _index()
    try:
        memory = discovery.load_memory(s)
        key = discovery.normalize_name(body.name)
        entry = (memory.get("suggestions") or {}).pop(key, None)
        discovery.save_memory(s, memory)
        if not entry:
            raise _err(404, "not_found")
        if not body.accept:
            memory = discovery.load_memory(s)
            memory["not_clients"] = sorted(set(memory.get("not_clients", [])) | {key})
            discovery.save_memory(s, memory)
            return {"ok": True}
        return discovery.apply(s, idx, [{"name": entry["name"], "accept": True, "recording_ids": entry["recording_ids"]}],
                               clear_proposal=False)
    finally:
        idx.close()


# -- Re-sorting existing recordings ----------------------------------------------------------


class ResortReq(BaseModel):
    scope: str = "unsorted"
    use_ai: bool = False


@app.post("/api/resort/preview")
def resort_preview(body: ResortReq):
    s, idx = _index()
    try:
        moves = resorter.preview(s, idx, "all_auto" if body.scope == "all_auto" else "unsorted", use_ai=body.use_ai and s.ai_ready)
    except ai.AIError as exc:
        raise _ai_err(exc) from exc
    finally:
        idx.close()
    return {"moves": moves}


class MovesReq(BaseModel):
    moves: list[dict]


@app.post("/api/resort/apply")
def resort_apply(body: MovesReq):
    s, idx = _index()
    try:
        return resorter.apply(s, idx, body.moves)
    except ValueError as exc:
        raise _err(400, "bad_name", str(exc)) from exc
    finally:
        idx.close()


class LogReq(BaseModel):
    log_id: str


@app.post("/api/resort/undo")
def resort_undo(body: LogReq):
    s, idx = _index()
    try:
        return {"restored": resorter.undo(s, idx, body.log_id)}
    except ValueError as exc:
        raise _err(400, "undo_failed", str(exc)) from exc
    finally:
        idx.close()


# -- Demo mode ------------------------------------------------------------------------------


@app.post("/api/demo/start")
def demo_start():
    if _sync_thread and _sync_thread.is_alive():
        raise _err(409, "busy")
    demo.start()
    _progress.clear()
    return get_state()


@app.post("/api/demo/stop")
def demo_stop():
    if _sync_thread and _sync_thread.is_alive():
        raise _err(409, "busy")
    demo.stop()
    _progress.clear()
    return get_state()
