"""Menu-bar (macOS) / system-tray (Windows) icon that keeps Pocket Bridge running.

The web server runs in a background thread; the icon owns the main thread
(required on macOS). If no tray is available (e.g. a Linux server without a
desktop), it falls back to running the web app in the foreground.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from . import autostart, instance
from .config import load_settings

log = logging.getLogger(__name__)

LABELS = {
    "nl": {"open": "Open Pocket Bridge", "sync": "Nu ophalen", "autostart": "Start bij inloggen", "quit": "Afsluiten", "last": "Laatst opgehaald", "setup": "Rond eerst de installatie af in Pocket Bridge."},
    "en": {"open": "Open Pocket Bridge", "sync": "Fetch now", "autostart": "Start at login", "quit": "Quit", "last": "Last fetched", "setup": "Finish the setup in Pocket Bridge first."},
}


def _icon_image():
    """The Striks bowtie (bundled PNG), shown in the menu bar / system tray."""
    from PIL import Image

    path = Path(__file__).parent / "web" / "static" / "img" / "striks-icon-color.png"
    img = Image.open(path).convert("RGBA")
    return img.resize((64, 64), Image.LANCZOS)


def _start_server(port: int) -> uvicorn.Server:
    from .web.app import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, name="pocket-web", daemon=True).start()
    for _ in range(100):  # wait until it accepts connections
        if server.started:
            break
        time.sleep(0.05)
    return server


def run(port: int, open_browser: bool = True) -> None:
    from .web import session

    url = f"http://127.0.0.1:{port}"
    server = _start_server(port)
    instance.register(port)
    if open_browser:
        webbrowser.open(session.login_url(port))

    try:
        import pystray
    except Exception as exc:  # no GUI backend available
        log.warning("tray not available (%s); running without icon", exc)
        _wait_forever(server)
        return

    lang = load_settings().language
    L = LABELS.get(lang, LABELS["en"])

    def last_sync_text(_item=None) -> str:
        from .sync import load_state

        try:
            s = load_settings()
            last = load_state(s).get("last_sync") or "-"
        except Exception:
            last = "-"
        return f"{L['last']}: {last.replace('T', ' ')[:16]}"

    def do_sync(icon, _item):
        import httpx

        try:
            r = httpx.post(f"{url}/api/sync", timeout=5, headers=session.headers())
            icon.notify(L["setup"] if r.status_code == 409 else L["sync"], "Pocket Bridge")
        except Exception:
            log.exception("sync from tray failed")

    def toggle_autostart(_icon, _item):
        autostart.set_enabled(not autostart.is_enabled())

    def quit_app(icon, _item):
        server.should_exit = True
        instance.unregister()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(L["open"], lambda *_: webbrowser.open(session.login_url(port)), default=True),
        pystray.MenuItem(L["sync"], do_sync),
        pystray.MenuItem(last_sync_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(L["autostart"], toggle_autostart, checked=lambda _i: autostart.is_enabled()),
        pystray.MenuItem(L["quit"], quit_app),
    )
    icon = pystray.Icon("pocket-bridge", _icon_image(), "Pocket Bridge by Striks", menu)

    if sys.platform == "darwin":
        try:  # menu-bar only, no Dock icon
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)
        except Exception:
            pass
    try:
        icon.run()
    except Exception as exc:
        log.warning("tray failed (%s); running without icon", exc)
        _wait_forever(server)
    finally:
        instance.unregister()


def _wait_forever(server: uvicorn.Server) -> None:
    try:
        while not server.should_exit:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True
    finally:
        instance.unregister()
