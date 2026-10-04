"""Register the local MCP server with Claude Desktop / show the Claude Code command."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "pocket-transcripts"
POCKET_OFFICIAL_MCP_URL = "https://public.heypocketai.com/mcp"


def claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def server_entry() -> dict:
    """How Claude should start our MCP server: the Python of this very environment."""
    return {"command": sys.executable, "args": ["-m", "pocket_bridge", "mcp"]}


def claude_code_command() -> str:
    exe = sys.executable
    quoted = f'"{exe}"' if " " in exe else exe
    return f"claude mcp add {SERVER_NAME} --scope user -- {quoted} -m pocket_bridge mcp"


def is_connected() -> bool:
    path = claude_desktop_config_path()
    if not path.exists():
        return False
    try:
        cfg = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return False
    return SERVER_NAME in (cfg.get("mcpServers") or {})


def connect_claude_desktop() -> Path:
    """Add (or update) our server in claude_desktop_config.json. Makes a backup first."""
    path = claude_desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{path} bevat ongeldige JSON; pas het handmatig aan. / contains invalid JSON; fix it by hand."
                ) from exc
        backup = path.with_name(f"claude_desktop_config.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(path, backup)
    cfg.setdefault("mcpServers", {})[SERVER_NAME] = server_entry()
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def manual_snippet() -> str:
    return json.dumps({"mcpServers": {SERVER_NAME: server_entry()}}, indent=2)
