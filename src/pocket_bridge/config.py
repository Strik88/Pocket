"""Settings: stored as JSON in the user's home directory (never inside the repo)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_POCKET_BASE_URL = "https://public.heypocketai.com/api/v1"
DEFAULT_MODEL = "claude-opus-5-5"


DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Project(BaseModel):
    """A project or engagement within a client; gets its own subfolder."""

    name: str
    keywords: list[str] = Field(default_factory=list)


class Client(BaseModel):
    """A client (klant). Recordings that match these rules land in the client's folder."""

    name: str
    keywords: list[str] = Field(default_factory=list)  # words/names in title or transcript
    pocket_tags: list[str] = Field(default_factory=list)  # Pocket tag names that mean "this client"
    email_domains: list[str] = Field(default_factory=list)  # e.g. acme.com; matched against meeting attendees
    projects: list[Project] = Field(default_factory=list)
    notes: str = ""  # free text; also given to Claude when classifying

    def find_project(self, name: str) -> Project | None:
        low = name.strip().lower()
        return next((p for p in self.projects if p.name.lower() == low), None)


class Onboarding(BaseModel):
    """Where the user is in the setup flow. Auto-sync waits until setup is completed."""

    step: str = "welcome"
    completed: bool = False
    claude_mode: str = ""  # "api" (key in the app), "desktop" (subscription via Claude Desktop) or "none"
    skipped: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    language: str = "nl"  # "nl" or "en"
    onboarding: Onboarding = Field(default_factory=Onboarding)

    # Who the user is: used to keep their own company out of client proposals
    user_name: str = ""
    own_domains: list[str] = Field(default_factory=list)

    # Demo mode: fictional sample conversations in a separate folder (see demo.py)
    demo_mode: bool = False

    pocket_api_key: str = ""
    pocket_base_url: str = DEFAULT_POCKET_BASE_URL
    data_dir: str = ""  # where the Markdown files go
    clients_dirname: str = "Klanten"
    unsorted_dirname: str = "_Ongesorteerd"
    clients: list[Client] = Field(default_factory=list)

    # Sync
    auto_sync: bool = True
    sync_interval_minutes: int = 15
    sync_since: str = ""  # YYYY-MM-DD; empty = everything
    keep_raw_json: bool = True

    # Claude (optional)
    anthropic_api_key: str = ""
    claude_model: str = DEFAULT_MODEL
    ai_classify: bool = True  # Claude sorts what the rules miss (only into existing clients)
    ai_client_status: bool = False  # Claude keeps a "current status" in each dossier (sends recent conversations; opt-in)
    keyword_min_hits: int = 2

    # Calendar: private iCal links (Google Calendar / Outlook) to match recordings to meetings
    calendar_urls: list[str] = Field(default_factory=list)
    calendar_margin_minutes: int = 15

    # Reports
    weekly_auto: bool = True  # write a weekly overview every Friday afternoon

    # Search on meaning (local embedding model, downloaded on first use)
    semantic_search: bool = False
    embed_model: str = DEFAULT_EMBED_MODEL

    @property
    def root(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else default_data_dir(self.language)

    @property
    def pocket_ready(self) -> bool:
        return bool(self.pocket_api_key.strip()) or self.demo_mode

    @property
    def anthropic_key(self) -> str:
        """The key Claude features use. A key from the ANTHROPIC_API_KEY environment variable only counts
        after the user chose "Claude in de app" during setup, so nothing is sent to Claude unasked."""
        key = self.anthropic_api_key.strip()
        if not key and self.onboarding.claude_mode == "api":
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        return key

    @property
    def ai_ready(self) -> bool:
        return bool(self.anthropic_key)

    def find_client(self, name: str) -> Client | None:
        low = name.strip().lower()
        return next((c for c in self.clients if c.name.lower() == low), None)


def config_dir() -> Path:
    override = os.environ.get("POCKET_BRIDGE_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "PocketBridge"
    return Path.home() / ".pocket-bridge"


def ensure_private_dir(path: Path) -> Path:
    """Create a folder only the user can open (keys, logs, caches). No-op for permissions on Windows."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def default_data_dir(language: str = "nl") -> Path:
    docs = Path.home() / "Documents"
    base = docs if docs.exists() else Path.home()
    return base / ("Pocket Transcripten" if language == "nl" else "Pocket Transcripts")


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "onboarding" not in raw and raw.get("pocket_api_key"):
            # Config from a version before the setup flow existed: the user is already set up.
            raw["onboarding"] = {"step": "done", "completed": True}
        return Settings.model_validate(raw)
    except Exception:
        # A broken config should never brick the app; keep a copy and start fresh.
        path.replace(path.with_name(f"config.broken-{int(time.time())}.json"))
        return Settings()


def save_settings(settings: Settings) -> None:
    path = config_path()
    ensure_private_dir(path.parent)
    data = json.dumps(settings.model_dump(), indent=2, ensure_ascii=False).encode("utf-8")
    # A unique temp file per write (the web app and the MCP server may save at the same moment),
    # created owner-only (mkstemp uses 0600): the API keys are never world-readable, not even briefly.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
