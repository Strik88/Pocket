"""Settings: stored as JSON in the user's home directory (never inside the repo)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_POCKET_BASE_URL = "https://public.heypocketai.com/api/v1"
DEFAULT_MODEL = "claude-opus-5-5"


class Client(BaseModel):
    """A client (klant). Recordings that match these rules land in the client's folder."""

    name: str
    keywords: list[str] = Field(default_factory=list)  # words/names in title or transcript
    pocket_tags: list[str] = Field(default_factory=list)  # Pocket tag names that mean "this client"
    notes: str = ""  # free text; also given to Claude when classifying


class Settings(BaseModel):
    language: str = "nl"  # "nl" or "en"
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
    ai_classify: bool = True
    ai_may_create_clients: bool = False
    keyword_min_hits: int = 2

    @property
    def root(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else default_data_dir(self.language)

    @property
    def pocket_ready(self) -> bool:
        return bool(self.pocket_api_key.strip())

    @property
    def ai_ready(self) -> bool:
        return bool(self.anthropic_api_key.strip() or os.environ.get("ANTHROPIC_API_KEY"))

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
        return Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        # A broken config should never brick the app; keep a copy and start fresh.
        path.replace(path.with_suffix(".broken.json"))
        return Settings()


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)  # contains API keys
    tmp.replace(path)
