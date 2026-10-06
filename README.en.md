# Pocket Bridge

**Bring your Pocket recordings to your own computer, organised per client and project automatically, and work with them together with Claude.**

[Nederlandse versie → README.md](README.md)

Pocket Bridge runs on your own computer and:

1. **connects to Pocket** through the official Pocket API (your API key);
2. **downloads every transcript** as a clean Markdown file with summary, action items, speakers and, if you connect your calendar, the matching meeting and attendees;
3. **creates a folder per client and project** and files each recording automatically, using your rules, your calendar and optionally Claude;
4. **keeps a dossier per client** with current status, all conversations and open action items;
5. **takes work off your hands**: one list of all action items, a briefing before a meeting, a follow-up e-mail after one, and a weekly overview every Friday;
6. **gives Claude access** to all transcripts, in the app itself and in Claude Desktop or Claude Code, with citations to the conversation an answer comes from.

Everything runs locally. Your transcripts, calendar link and keys stay on your machine.

![Dashboard](docs/img/dashboard.png)

## Quick start

1. **Download**: on GitHub click **Code → Download ZIP**, unzip, and move the folder somewhere permanent such as your home folder (not Downloads).
2. **Start**: double-click **`start-mac.command`** (Mac) or **`start-windows.bat`** (Windows). The first run installs [uv](https://docs.astral.sh/uv/) (it manages Python for you) and the dependencies, which takes a few minutes. Your browser then opens **http://127.0.0.1:8765** and an **orange dot appears in the menu bar** (Mac) or **an icon in the system tray** (Windows), with *Open*, *Sync now*, *Start at login* and *Quit*. You can close the terminal window.
   - Mac says it can't be opened? Right-click → **Open** → **Open**, or run `chmod +x start-mac.command`.
   - Windows SmartScreen? **More info → Run anyway**.
3. **Follow the setup guide** (switch to EN at the bottom left): Pocket API key (*Settings → Developer → API Keys* in Pocket), folder, clients, optional Anthropic API key, connect Claude Desktop. Then under **Settings** connect your calendar, turn on search on meaning and choose whether to start at login.

## Step by step on Windows

1. **Download**: on github.com/Strik88/Pocket click **Code → Download ZIP**.
2. **Extract (important)**: right-click `Pocket-main.zip` → **Extract All…** → choose `C:\Users\<you>\PocketBridge`. Don't start anything from inside the ZIP window.
3. **Start**: open the extracted `Pocket-main` folder and double-click **`start-windows.bat`** (type *Windows Batch File*). Blue *"Windows protected your PC"* screen? **More info → Run anyway**. The first run installs uv and the dependencies in a black window; this takes a few minutes.
4. Your browser opens **http://127.0.0.1:8765** and an **orange dot** appears in the system tray (bottom right; click **^** if hidden).
5. Follow the setup guide and click **Connect Claude Desktop**.
6. **Fully quit Claude Desktop** (right-click its tray icon → **Quit**) and reopen it; `pocket-transcripts` appears under **Settings → Developer**. Both the regular and the Microsoft Store version of Claude Desktop are supported.
7. Turn on **Settings → Always on → Start at login**.

Transcripts: `Documents\Pocket Transcripts`. Settings and keys: `%APPDATA%\PocketBridge`. Log: `%APPDATA%\PocketBridge\pocket-bridge.log`. To update: quit via the tray icon, extract the new ZIP over the old folder, run `start-windows.bat` again; settings and transcripts are kept.

## Features

- **Folders per client and project**: `Clients/<Client>/[<Project>/]<year>/…md`, plus `_Dossier.md`, `_Briefings/` and `_Follow-ups/` per client and `_Weekly/` at the top. Drag files between folders and Pocket Bridge follows; after a move it **suggests keywords** so similar recordings are filed automatically next time.
- **Calendar**: paste your calendar's secret iCal link (Google Calendar: *Settings → your calendar → Secret address in iCal format*; Outlook: *Settings → Calendar → Shared calendars → Publish a calendar*). Each recording is matched to the meeting at that time; title and attendees are added, and a client's **e-mail domain** (e.g. `acme.com`) files meetings with their people automatically.
- **Action items**: one page with every open item, per client. Ticking is saved in the conversation and the dossier, survives updates from Pocket, and ticks made in `_Dossier.md` are carried back.
- **Reports with Claude**: meeting prep briefing, follow-up e-mail (opens in your mail app), weekly overview (automatically every Friday afternoon; works without Claude too), and a "current status" note per client.
- **Speakers**: rename "Speaker 1" to real names, or let Claude guess from the conversation and attendees. Remembered across updates.
- **Search on meaning**: a multilingual model (~220 MB, downloaded once) runs locally and finds conversations that use different words, combined with keyword search.
- **Ask Claude**: answers stream in as Claude writes, with numbered citations; hover for the quote, click to open the conversation.
- **Always on**: start at login with the tray icon; a second start just opens the running app.

## In Claude Desktop and Claude Code

No Anthropic API key needed: Claude Desktop does the thinking. Tools: `list_clients`, `list_recordings`, `search_transcripts`, `get_transcript`, `get_client_dossier`, `open_action_items`, `complete_action_item`, `weekly_overview`, `get_speakers`, `rename_speakers`, `assign_recording`, `add_client`, `sync_now`, `status`. Ready-made prompts: *Meeting prep*, *Follow-up e-mail*, *Weekly review*. Suggested project instructions: [docs/claude-instructies.md](docs/claude-instructies.md).

For Claude Code, the exact command is shown in step 5 of the setup guide:

```bash
claude mcp add pocket-transcripts --scope user -- /path/to/PocketBridge/.venv/bin/python -m pocket_bridge mcp
```

## Sorting order (new recordings only)

Pocket tag → calendar meeting (attendee e-mail domain, or client name in the meeting title) → keyword in title → keywords at least N× in summary/transcript (clear winner) → Claude (if enabled, only when confident) → `_Unsorted`. Then a project within the client if its name/keywords appear.

## Privacy

Keys and the calendar link live in your user folder (`~/.pocket-bridge/config.json`, or `%APPDATA%\PocketBridge\config.json` on Windows), never in this repository. The web page only listens on 127.0.0.1. Search on meaning runs locally. Every Claude feature can be switched off.

When Pocket updates a conversation the file is rewritten: ticked action items, speaker names, client and project are kept, but notes typed into the conversation file are not. Keep notes in `_Dossier.md` under **Notes**.

## Development

```bash
uv sync --extra dev && uv run pytest
uv run pocket-bridge tray      # web app + tray icon
```

Not affiliated with Pocket or Anthropic. MIT licence.
