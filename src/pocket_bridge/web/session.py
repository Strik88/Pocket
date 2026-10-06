"""Access to the local web app: only the browser that the app itself opened (or a process with the key file).

The app listens on 127.0.0.1, which every program and every user account on the
computer can reach. A random key, stored in a file only the user can read,
protects the API: the app opens the browser at /auth?t=<key>, which sets an
HttpOnly cookie. Calls without that cookie (or the X-Pocket-Bridge-Token
header, for the tray icon) are refused.
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys

from ..config import config_dir

COOKIE = "pb_session"
_token: str | None = None


def _path():
    return config_dir() / "session.key"


def token() -> str:
    """The key for this computer and user; created on first use and kept across restarts."""
    global _token
    if _token:
        return _token
    p = _path()
    try:
        value = p.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if len(value) < 32:
        value = secrets.token_urlsafe(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        if sys.platform != "win32":
            os.chmod(p, 0o600)
    _token = value
    return value


def valid(candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(str(candidate), token())


def login_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/auth?t={token()}"


def headers() -> dict[str, str]:
    """For local callers such as the tray icon."""
    return {"X-Pocket-Bridge-Token": token(), "X-Pocket-Bridge": "1"}
