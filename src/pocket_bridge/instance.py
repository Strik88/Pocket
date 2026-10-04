"""Keep one running copy: a second start just opens the browser on the first one."""

from __future__ import annotations

import json
import os

import httpx

from .config import config_dir


def _path():
    return config_dir() / "instance.json"


def running_port() -> int | None:
    p = _path()
    if not p.exists():
        return None
    try:
        port = int(json.loads(p.read_text())["port"])
        r = httpx.get(f"http://127.0.0.1:{port}/api/ping", timeout=1.5)
        if r.status_code == 200 and r.json().get("app") == "pocket-bridge":
            return port
    except Exception:
        pass
    return None


def register(port: int) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": port, "pid": os.getpid()}))


def unregister() -> None:
    p = _path()
    try:
        if p.exists() and json.loads(p.read_text()).get("pid") == os.getpid():
            p.unlink()
    except Exception:
        pass
