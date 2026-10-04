"""Local MCP server: lets Claude (Desktop / Code) browse, search and organise the transcripts.

Runs over stdio. Claude Desktop starts it automatically once it is registered
(see claude_connect.py). While it runs, it also keeps syncing in the background
when auto-sync is on, so new recordings show up without opening the web app.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import sync as syncmod
from .config import Client, load_settings, save_settings
from .dossier import DOSSIER_NAME
from .index import Index
from .storage import client_dir

INSTRUCTIONS = """Tools for the user's Pocket recordings (meeting transcripts), stored locally as Markdown and organised per client.
Typical flow: list_clients or search_transcripts to find conversations, then get_transcript for the full text.
Use get_client_dossier for a client overview with open action items. Recordings are referenced by Pocket id or title.
Always mention the title and date of the conversations you base an answer on."""

server = MCPServer(name="pocket-transcripts", instructions=INSTRUCTIONS)


def _index() -> tuple:
    settings = load_settings()
    idx = Index(settings)
    idx.refresh()
    return settings, idx


def _fmt_row(r) -> str:
    extra = f" · {r.duration_minutes} min" if r.duration_minutes else ""
    acts = f" · {r.open_actions} open" if r.open_actions else ""
    return f"- {(r.date or '')[:16].replace('T', ' ')} · **{r.title}** · {r.client or '(ongesorteerd/unsorted)'}{extra}{acts} · id `{r.pocket_id}`"


@server.tool()
def status() -> str:
    """Show whether Pocket is connected, where files are stored and when the last sync ran."""
    settings = load_settings()
    state = syncmod.load_state(settings) if settings.root.exists() else {}
    last = (state or {}).get("last_result") or {}
    return "\n".join(
        [
            f"Pocket connected: {'yes' if settings.pocket_ready else 'NO - open the Pocket Bridge app to add your API key'}",
            f"Folder: {settings.root}",
            f"Clients configured: {len(settings.clients)}",
            f"Auto-sync: {'on' if settings.auto_sync else 'off'} (every {settings.sync_interval_minutes} min)",
            f"Last sync: {(state or {}).get('last_sync') or 'never'} — {last.get('message', '')}",
        ]
    )


@server.tool()
def list_clients() -> str:
    """List all clients with number of conversations, last contact date and open action items."""
    settings, idx = _index()
    try:
        stats = {s["client"]: s for s in idx.client_stats()}
        lines = []
        names = [c.name for c in settings.clients] + [n for n in stats if n and not settings.find_client(n)]
        for name in names:
            s = stats.get(name, {})
            lines.append(f"- **{name}**: {s.get('recordings', 0)} gesprekken/conversations, last {(s.get('last_date') or '-')[:10]}, {s.get('open_actions') or 0} open actions")
        if None in stats:
            lines.append(f"- _(unsorted)_: {stats[None]['recordings']} conversations")
        return "\n".join(lines) or "No clients yet."
    finally:
        idx.close()


@server.tool()
def list_recordings(client: str = "", since: str = "", until: str = "", limit: int = 30, unsorted_only: bool = False) -> str:
    """List recordings, newest first. Filter by client name, date range (YYYY-MM-DD) or only unsorted ones."""
    _, idx = _index()
    try:
        rows = idx.list(client=client or None, since=since, until=until, limit=limit, unsorted_only=unsorted_only)
        return "\n".join(_fmt_row(r) for r in rows) or "No recordings found."
    finally:
        idx.close()


@server.tool()
def search_transcripts(query: str, client: str = "", limit: int = 10) -> str:
    """Full-text search across titles, summaries and transcripts. Use "quotes" for exact phrases.
    Returns matching conversations with a snippet. Optionally restrict to one client."""
    _, idx = _index()
    try:
        hits = idx.search(query, client=client or None, limit=limit)
        if not hits:
            hits = idx.search(query, client=client or None, limit=limit, any_word=True)
        if not hits:
            return "No matches. Try fewer or different words."
        return "\n".join(
            f"- {(h['date'] or '')[:10]} · **{h['title']}** · {h['client'] or '(unsorted)'} · id `{h['pocket_id']}`\n  > {h['snippet']}"
            for h in hits
        )
    finally:
        idx.close()


@server.tool()
def get_transcript(recording: str) -> str:
    """Full Markdown of one recording (summary, action items, transcript). Accepts Pocket id, exact title or a unique part of the title."""
    _, idx = _index()
    try:
        row = idx.find(recording)
        if not row:
            return f"Recording not found: {recording}. Use search_transcripts or list_recordings first."
        return Path(row.path).read_text(encoding="utf-8")
    finally:
        idx.close()


@server.tool()
def get_client_dossier(client: str) -> str:
    """The client's dossier: number of conversations, last contact, open action items and a list of all conversations."""
    settings = load_settings()
    known = settings.find_client(client)
    path = client_dir(settings, known.name if known else client) / DOSSIER_NAME
    if not path.exists():
        return f"No dossier for '{client}'. Known clients: {', '.join(c.name for c in settings.clients) or 'none'}"
    return path.read_text(encoding="utf-8")


@server.tool()
def open_action_items(client: str = "") -> str:
    """All unchecked action items across conversations, optionally for one client."""
    from .storage import parse_markdown

    _, idx = _index()
    try:
        out = []
        for r in idx.list(client=client or None, limit=100_000):
            if not r.open_actions:
                continue
            parsed = parse_markdown(Path(r.path))
            for done, text in parsed.action_items if parsed else []:
                if not done:
                    out.append(f"- [ ] {text} — {r.title} ({(r.date or '')[:10]}, {r.client or 'unsorted'})")
        return "\n".join(out) or "No open action items."
    finally:
        idx.close()


@server.tool()
def sync_now(full: bool = False) -> str:
    """Fetch new recordings from Pocket now. full=True re-checks everything instead of only recent ones."""
    return syncmod.run_sync(load_settings(), full=full).message


@server.tool()
def assign_recording(recording: str, client: str) -> str:
    """Move a recording to a client's folder (creates the client if new). Use client="" to move it to Unsorted."""
    try:
        path = syncmod.assign(load_settings(), recording, client or None)
    except ValueError as exc:
        return str(exc)
    return f"Moved to {path}"


@server.tool()
def add_client(name: str, keywords: list[str] | None = None, pocket_tags: list[str] | None = None, notes: str = "") -> str:
    """Add a client, or update its keywords/tags/notes. Keywords (names, company, project) are used to sort new recordings."""
    settings = load_settings()
    existing = settings.find_client(name)
    if existing:
        existing.keywords = sorted(set(existing.keywords) | set(keywords or []))
        existing.pocket_tags = sorted(set(existing.pocket_tags) | set(pocket_tags or []))
        if notes:
            existing.notes = notes
    else:
        settings.clients.append(Client(name=name.strip(), keywords=keywords or [], pocket_tags=pocket_tags or [], notes=notes))
    save_settings(settings)
    return f"Client '{name}' saved. Note: existing recordings are not moved automatically; use assign_recording."


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    syncmod.AutoSync().start()
    server.run("stdio")


if __name__ == "__main__":
    main()
