"""Command line entry point.

    pocket-bridge            start the web app and open it in the browser (default)
    pocket-bridge mcp        run the MCP server over stdio (Claude starts this itself)
    pocket-bridge sync       sync once from the terminal   (--full to re-check everything)
    pocket-bridge connect    register with Claude Desktop
    pocket-bridge rebuild    re-scan files and regenerate dossiers
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import webbrowser


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free port found")


def cmd_web(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import app

    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Pocket Bridge draait op / is running at: {url}\n  Sluit dit venster om te stoppen. / Close this window to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pocket-bridge", description="Pocket -> local Markdown per client -> Claude")
    sub = parser.add_subparsers(dest="cmd")
    web = sub.add_parser("web", help="start the web app (default)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true")
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
    {"web": cmd_web, "mcp": cmd_mcp, "sync": cmd_sync, "connect": cmd_connect, "rebuild": cmd_rebuild}[args.cmd](args)


if __name__ == "__main__":
    main()
