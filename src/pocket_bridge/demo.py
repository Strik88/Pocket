"""Demo mode: try Pocket Bridge with fictional Dutch sample conversations, without a Pocket device.

The real flow runs unchanged: "fetching" goes through the normal sync code with a
fake Pocket API that serves the bundled conversations, into a separate demo
folder. With a Claude key everything is live; without one, client discovery,
"Vraag Claude", the briefing and the follow-up use pre-written results that the
app labels as an example. Leaving demo mode restores the previous settings and
removes the demo folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import httpx

from .config import Onboarding, Settings, config_dir, config_path, load_settings, save_settings

DATA_FILE = Path(__file__).parent / "demo" / "conversations.json"
MARKER = ".pocket-bridge-demo"


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def demo_dir() -> Path:
    return config_dir() / "demo-gesprekken"


def _backup_path() -> Path:
    return config_dir() / "settings-before-demo.json"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def _wipe_demo_dir() -> None:
    d = demo_dir()
    if d.exists() and (d / MARKER).exists():  # only ever delete our own folder
        shutil.rmtree(d, ignore_errors=True)


def start() -> Settings:
    """Switch to demo mode. Keeps the Claude key and language; everything else starts fresh."""
    current = load_settings()
    if current.demo_mode:
        return current
    if config_path().exists():
        _write_private(_backup_path(), config_path().read_text(encoding="utf-8"))
    _wipe_demo_dir()
    demo_dir().mkdir(parents=True, exist_ok=True)
    (demo_dir() / MARKER).write_text("Voorbeeldgesprekken van Pocket Bridge. Deze map wordt verwijderd als je de demo verlaat.\n", encoding="utf-8")
    s = Settings(
        language=current.language,
        anthropic_api_key=current.anthropic_api_key,
        claude_model=current.claude_model,
        demo_mode=True,
        data_dir=str(demo_dir()),
        auto_sync=False,
        user_name=data()["owner"]["name"],
        clients_dirname="Klanten" if current.language == "nl" else "Clients",
        unsorted_dirname="_Ongesorteerd" if current.language == "nl" else "_Unsorted",
        onboarding=Onboarding(step="claude", claude_mode=current.onboarding.claude_mode if current.anthropic_api_key else ""),
    )
    save_settings(s)
    return s


def stop() -> Settings:
    """Leave demo mode: restore the settings from before and remove the demo folder."""
    backup = _backup_path()
    s = load_settings()
    keep_key = s.anthropic_api_key  # a key entered during the demo is worth keeping
    if backup.exists():
        _write_private(config_path(), backup.read_text(encoding="utf-8"))
        backup.unlink()
        s = load_settings()
    else:
        s = Settings(language=s.language)
    s.demo_mode = False
    s.anthropic_api_key = keep_key  # the key as it is now: added in the demo, or deliberately removed there
    save_settings(s)
    _wipe_demo_dir()
    return s


# -- Fake Pocket API ----------------------------------------------------------------------


def transport() -> httpx.MockTransport:
    """Serves the sample conversations in the shape of the Pocket public API."""
    shift = date_shift()
    recs = {}
    for r in data()["recordings"]:
        rec = {k: v for k, v in r.items() if k != "meeting"}
        for key in ("recording_at", "updated_at"):
            if shift and rec.get(key):
                rec[key] = (datetime.fromisoformat(rec[key].replace("Z", "+00:00")) + shift).strftime("%Y-%m-%dT%H:%M:%SZ")
        recs[r["id"]] = rec

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/public/recordings"):
            limit = int(request.url.params.get("limit", 50))
            page = int(request.url.params.get("page", 1))
            items = sorted(recs.values(), key=lambda r: r["recording_at"], reverse=True)
            light = [{k: v for k, v in r.items() if k not in ("transcript", "summarizations")} for r in items]
            chunk = light[(page - 1) * limit : page * limit]
            return httpx.Response(200, json={"success": True, "data": chunk, "pagination": {
                "page": page, "limit": limit, "total": len(light), "total_pages": -(-len(light) // limit),
                "has_more": page * limit < len(light)}})
        rid = path.rsplit("/", 1)[-1]
        if rid in recs:
            return httpx.Response(200, json={"success": True, "data": recs[rid]})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def date_shift() -> timedelta:
    """Whole weeks to move the sample conversations forward, so the newest one is always recent
    (weekdays stay the same, so "maandag" in a transcript is still a Monday)."""
    newest = max(datetime.fromisoformat(r["recording_at"].replace("Z", "+00:00")) for r in data()["recordings"])
    gap = (datetime.now(timezone.utc) - newest).days
    return timedelta(weeks=gap // 7)  # never into the future: the newest ends up 0 to 6 days ago


def total() -> int:
    return len(data()["recordings"])


def meeting_for(pocket_id: str) -> dict | None:
    for r in data()["recordings"]:
        if r["id"] == pocket_id:
            return r.get("meeting") or None
    return None


# -- Pre-written results (used when there is no Claude key) --------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def raw_proposal() -> dict:
    return data()["proposal"]


def answer(question: str) -> list[dict] | None:
    """Blocks with citations for one of the example questions (citations by pocket_id)."""
    q = _norm(question)
    for a in data().get("answers", []):
        if _norm(a["question"]) == q:
            return a["blocks"]
    return None


def questions() -> list[str]:
    return [a["question"] for a in data().get("answers", [])]


def briefing(client: str) -> str | None:
    for name, text in data().get("briefings", {}).items():
        if _norm(name) == _norm(client):
            return text
    return None


def followup(pocket_id: str) -> dict | None:
    return data().get("followups", {}).get(pocket_id)


def write_statuses(settings, index, names: list[str]) -> None:
    """Give the demo clients a 'stand van zaken', as Claude would keep it (only in demo mode)."""
    from .dossier import build_dossier, status_path

    statuses = data().get("statuses", {})
    for name in names:
        text = next((v for k, v in statuses.items() if _norm(k) == _norm(name)), None)
        if text:
            status_path(settings, name).write_text(text.strip() + "\n", encoding="utf-8")
            build_dossier(settings, name, index.list(client=name, limit=100_000))
