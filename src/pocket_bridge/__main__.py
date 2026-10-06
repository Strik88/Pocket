"""Command line entry point.

    pocket-bridge            start the web app and open it in the browser (default)
    pocket-bridge tray       same, with a menu-bar / system-tray icon (used by the start scripts)
    pocket-bridge mcp        run the MCP server over stdio (Claude starts this itself)
    pocket-bridge sync       sync once from the terminal   (--full to re-check everything)
    pocket-bridge connect    register with Claude Desktop
    pocket-bridge rebuild    re-scan files and regenerate dossiers
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser


def quiet_libraries() -> None:
    """httpx logs every URL at INFO, including secret calendar links: keep those out of the logs."""
    for name in ("httpx", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free port found")


def _already_running(args: argparse.Namespace) -> bool:
    from . import instance

    port = instance.running_port()
    if port:
        print(f"Pocket Bridge draait al / is already running: http://127.0.0.1:{port}")
        if not args.no_browser:
            from .web import session

            webbrowser.open(session.login_url(port))
        return True
    return False


def cmd_tray(args: argparse.Namespace) -> None:
    from . import tray
    from .config import config_dir, ensure_private_dir

    if _already_running(args):
        return
    log_file = ensure_private_dir(config_dir()) / "pocket-bridge.log"
    os.close(os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))  # owner-only from the start
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    tray.run(_free_port(args.port), open_browser=not args.no_browser)


def cmd_web(args: argparse.Namespace) -> None:
    import uvicorn

    from . import instance
    from .web.app import app

    if _already_running(args):
        return
    from .web import session

    port = _free_port(args.port)
    instance.register(port)
    url = session.login_url(port)
    print(f"\n  Pocket Bridge draait op / is running at: {url}\n  Sluit dit venster om te stoppen. / Close this window to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        instance.unregister()


def cmd_mcp(args: argparse.Namespace) -> None:
    from .mcp_server import main

    main()


def cmd_sync(args: argparse.Namespace) -> None:
    from .sync import run_sync

    res = run_sync(full=args.full, progress=print)
    for err in res.errors:
        print("  !", err, file=sys.stderr)


def cmd_connect(args: argparse.Namespace) -> None:
    from . import claude_connect

    path = claude_connect.connect_claude_desktop()
    print(f"Claude Desktop config bijgewerkt / updated: {path}")
    print("Herstart Claude Desktop. / Restart Claude Desktop.")
    print(f"\nVoor Claude Code / For Claude Code:\n  {claude_connect.claude_code_command()}")


def cmd_rebuild(args: argparse.Namespace) -> None:
    from .config import load_settings
    from .sync import rebuild

    print(rebuild(load_settings()))


def _ensure_streams() -> None:
    """pythonw.exe (Windows, no console) has no stdout/stderr; uvicorn and print() need them."""
    if sys.stdout is None or sys.stderr is None:
        from .config import config_dir

        config_dir().mkdir(parents=True, exist_ok=True)
        log = open(config_dir() / "pocket-bridge.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log


def main(argv: list[str] | None = None) -> None:
    _ensure_streams()
    parser = argparse.ArgumentParser(prog="pocket-bridge", description="Pocket -> local Markdown per client -> Claude")
    sub = parser.add_subparsers(dest="cmd")
    web = sub.add_parser("web", help="start the web app (default)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true")
    tray = sub.add_parser("tray", help="web app + menu-bar/tray icon")
    tray.add_argument("--port", type=int, default=8765)
    tray.add_argument("--no-browser", action="store_true")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    sync = sub.add_parser("sync", help="sync once")
    sync.add_argument("--full", action="store_true")
    sub.add_parser("connect", help="register with Claude Desktop")
    sub.add_parser("rebuild", help="re-index files and rebuild dossiers")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in sub.choices and argv[0] not in ("-h", "--help")):
        argv.insert(0, "web")  # default command
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    quiet_libraries()
    {"web": cmd_web, "tray": cmd_tray, "mcp": cmd_mcp, "sync": cmd_sync, "connect": cmd_connect, "rebuild": cmd_rebuild}[args.cmd](args)


if __name__ == "__main__":
    main()
