# Pocket Bridge

**Bring your Pocket recordings to your own computer, organised per client automatically, and talk to them with Claude.**

[Nederlandse versie → README.md](README.md)

Pocket Bridge is a small local app that:

1. **connects to Pocket** through the official Pocket API (your API key);
2. **downloads every transcript** as a clean Markdown file into a folder on your computer;
3. **creates a folder per client** and files each recording automatically: first by your rules (keywords, Pocket tags), optionally with Claude when the rules find nothing;
4. **keeps a dossier per client** (`_Dossier.md`) with all conversations, last contact and open action items;
5. **gives Claude access** to all transcripts through a local MCP server, so in Claude Desktop or Claude Code you can ask: *"What did we agree with Acme last month?"*

Everything runs locally. Your transcripts and keys stay on your machine.

![Dashboard](docs/img/dashboard.png)

## Quick start

1. **Download**: on GitHub click **Code → Download ZIP**, unzip, and move the folder somewhere permanent such as your home folder (not Downloads: Claude Desktop will start the app from here).
2. **Start**: double-click **`start-mac.command`** (Mac) or **`start-windows.bat`** (Windows). The first run installs [uv](https://docs.astral.sh/uv/) (it manages Python for you) and the dependencies, which takes about a minute. Your browser then opens **http://127.0.0.1:8765**.
   - Mac says it can't be opened? Right-click → **Open** → **Open**, or run `chmod +x start-mac.command`.
   - Windows SmartScreen? **More info → Run anyway**.
3. **Follow the setup guide** (switch to EN at the bottom left):
   1. Pocket: *Settings → Developer → API Keys*, create a key (`pk_…`), paste, *Test & save*.
   2. Choose the folder for your transcripts.
   3. Add clients with keywords (company, contact names, project names).
   4. Optional: an Anthropic API key for smart sorting and asking questions in the app.
   5. Connect Claude Desktop with one click, then restart Claude Desktop.

Click **Sync now** and your recordings appear.

## On disk

```
Pocket Transcripts/
├── Clients/<Client>/_Dossier.md
├── Clients/<Client>/2026/2026-09-01 0930 Kickoff.md
├── _Unsorted/2026/...
└── .pocket-bridge/      (index and cache)
```

Each file has front-matter metadata, the summary, action items as a checklist and the full transcript with speakers and timestamps. Works in any editor and as an [Obsidian](https://obsidian.md) vault. Drag a file into another client folder and Pocket Bridge follows; your own notes under **Notes** in `_Dossier.md` are preserved.

## In Claude

Tools: `list_clients`, `list_recordings`, `search_transcripts`, `get_transcript`, `get_client_dossier`, `open_action_items`, `assign_recording`, `add_client`, `sync_now`, `status`. Suggested project instructions: [docs/claude-instructies.md](docs/claude-instructies.md).

For Claude Code, the exact command is shown in step 5 of the setup guide:

```bash
claude mcp add pocket-transcripts --scope user -- /path/to/PocketBridge/.venv/bin/python -m pocket_bridge mcp
```

Pocket's own MCP server (`https://public.heypocketai.com/mcp`) can be added alongside it as a custom connector.

## Sorting order (new recordings only)

Pocket tag → keyword in title → keywords at least N× in summary/transcript (clear winner) → Claude (if enabled, only when confident) → `_Unsorted`.

## Auto-sync

Every 15 minutes (configurable) while the web app **or** Claude Desktop is open. From a terminal: `.venv/bin/python -m pocket_bridge sync [--full]`.

## Privacy

Keys live in your user folder (`~/.pocket-bridge/config.json`, or `%APPDATA%\PocketBridge\config.json` on Windows), never in this repository. The web page only listens on 127.0.0.1.

## Development

```bash
uv sync --extra dev && uv run pytest
```

Not affiliated with Pocket or Anthropic. MIT licence.
