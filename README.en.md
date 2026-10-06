![Pocket Bridge by Striks](docs/img/hero.png)

# Pocket Bridge by Striks

**Your client conversations from Pocket, organised per client automatically. With Claude as your assistant for every next conversation.**

[Nederlandse versie → README.md](README.md)

- **Claude proposes your clients.** No typing in client lists. Claude reads your conversations and proposes clients and projects; you decide what goes in.
- **Everything stays on your computer.** A folder per client with plain text files, a dossier with the current status and all action items. Works as an Obsidian vault too.
- **Claude takes it from there.** A briefing before your next meeting, a follow-up e-mail after one, and answers to your questions with sources. In the app, or in Claude Desktop with your own subscription.

![How it works: fetch conversations, Claude proposes clients, you confirm, then ask questions with sources](docs/img/walkthrough.gif)

### Try it without Pocket

On the welcome screen choose **Look around with sample conversations first**. You walk through the whole setup with sixteen made-up conversations of a consultancy (in Dutch), including Claude's proposal, the client pages, a briefing and questions with sources. No Pocket and no API key needed. The demo uses a separate folder; leaving it restores everything.

### Privacy in short

- Your conversations are plain files on your own computer, in a folder you choose.
- Pocket Bridge only reads from Pocket. Nothing changes in your Pocket account.
- Pocket Bridge only sends text to Claude when you use a Claude feature, and only what it needs. To propose clients that is a short summary per conversation, not the full transcript.
- The app runs on your computer and only opens in the browser the program opens itself.

## Install

1. **Download**: on GitHub click **Code → Download ZIP**, unzip, and move the folder somewhere permanent such as your home folder (not Downloads). Or `git clone https://github.com/strik88/pocket.git PocketBridge`.
2. **Start**: double-click **`start-mac.command`** (Mac) or **`start-windows.bat`** (Windows). The first run installs [uv](https://docs.astral.sh/uv/) (it manages Python for you) and the dependencies, which takes a few minutes. Your browser then opens and the **Striks bowtie** appears in the menu bar (Mac) or near the clock (Windows), with *Open*, *Fetch now*, *Start at login* and *Quit*.
   - Mac says it can't be opened? Right-click → **Open** → **Open**, or run `chmod +x start-mac.command`.
   - Windows SmartScreen? **More info → Run anyway**.
3. **Walk through the setup** (choose English on the welcome screen). Three phases, seven short steps, about ten minutes:
   - **Connect**: your Pocket API key (*Settings → Developer → API Keys* in Pocket), then choose how you use Claude: an Anthropic API key, your Claude subscription through Claude Desktop, or not now.
   - **Organise**: fetch your conversations, let Claude propose your clients (nothing changes until you confirm, and you can undo), then sort what is left.
   - **Finish**: connect Claude Desktop, and extras such as fetching automatically, starting at login, search by meaning and your calendar.

### Windows, step by step

1. On github.com/Strik88/Pocket click **Code → Download ZIP**.
2. **Extract (important)**: right-click `Pocket-main.zip` → **Extract All…** → `C:\Users\<you>\PocketBridge`. Don't start anything from inside the ZIP window.
3. Open the extracted `Pocket-main` folder and double-click **`start-windows.bat`**. Blue *"Windows protected your PC"* screen? **More info → Run anyway**.
4. The browser opens the welcome screen; the bowtie appears near the clock (click **^** if hidden).
5. After connecting Claude Desktop, **fully quit** it (right-click its icon near the clock → **Quit**) and reopen it. Both the regular and the Microsoft Store version are supported.

Conversations: `Documents\Pocket Transcripts`. Settings and keys: `%APPDATA%\PocketBridge`. Log: `%APPDATA%\PocketBridge\pocket-bridge.log`. To update: quit via the bowtie, extract the new ZIP over the old folder and start again; settings and conversations are kept.

## Claude: API key or subscription

| | Anthropic API key | Claude subscription (Pro or Max) |
|---|---|---|
| Propose clients | In the app, with a cost estimate first | In Claude Desktop: pick *Propose my clients*; the proposal shows up in the app |
| Briefing, follow-up, questions with sources | In the app | In Claude Desktop, through Pocket Bridge's tools |
| Cost | Per use, usually a few cents per conversation | Included in your subscription |

A Claude subscription cannot be used directly by other apps, so that route goes through Claude Desktop. Create an API key at [console.anthropic.com](https://console.anthropic.com) and add a few euros of credit under Billing.

## Features

![Overview](docs/img/overview.png)

- **Overview**: what is new, what needs attention, your numbers and a quick way into Claude.
- **Clients**: a page per client with the current status, open action items, conversations per project, and **Briefing for next meeting**.
- **Ask Claude**: answers stream in with numbered sources and the exact quotes. Click a source to open the conversation.
- **Conversations**: search, read and move; each one shows why it was filed where it is. Write a **follow-up e-mail** and **name the speakers**.
- **Action items**, a **weekly overview** every Friday, your **calendar** (secret iCal link), **search by meaning** (local model, about 220 MB) and **always on** with the bowtie icon.

![Ask Claude with sources](docs/img/ask.png)

## In Claude Desktop and Claude Code

No API key needed. Tools: `list_clients`, `list_recordings`, `search_transcripts`, `get_transcript`, `get_client_dossier`, `open_action_items`, `complete_action_item`, `weekly_overview`, `get_speakers`, `rename_speakers`, `get_client_discovery_material`, `submit_client_proposal`, `apply_client_proposal`, `assign_recording`, `add_client`, `sync_now`, `status`. Ready-made tasks in the menu: *Propose my clients*, *Meeting prep*, *Follow-up e-mail*, *Weekly review*.

For Claude Code, the exact command is under *Settings → Connections* in the app:

```bash
claude mcp add pocket-transcripts --scope user -- /path/to/PocketBridge/.venv/bin/python -m pocket_bridge mcp
```

## Sorting order (new conversations)

Pocket tag → calendar meeting (attendee e-mail domain, or client name in the meeting title) → client name or keyword in the title → keywords clearly more often than other clients' → Claude (if enabled, existing clients only, only when confident) → `_Unsorted`. New organisations Claude notices become a suggestion on the overview; clients are never created without you.

## Privacy, security and cost

- Keys and the calendar link live in your user folder (`~/.pocket-bridge/config.json`, or `%APPDATA%\PocketBridge\config.json`), in a file only you can read. Never in this repository.
- The app only listens on 127.0.0.1 and requires a session key that only the browser opened by the program gets, so other programs and websites cannot read your conversations.
- Text from conversations and calendar invites is always treated by Claude as data, never as instructions.
- Calendar links never go into log files; the calendar cache lives outside your (often shared) conversations folder.
- Proposing clients with an API key usually costs a few tens of cents per hundred conversations; you see the estimate first. Keeping a status per client is off by default.

When Pocket updates a conversation the file is rewritten: ticked action items, speaker names, client and project are kept, notes typed into the conversation file are not. Keep notes in `_Dossier.md` under **Notes**.

## Development

```bash
uv sync --extra dev && uv run pytest
uv run pocket-bridge tray      # web app + tray icon
uv run pocket-bridge mcp       # MCP server (stdio)
```

Made by Ian Strik · [Striks AI Consulting](https://striksaiconsulting.com). Not affiliated with Pocket or Anthropic. MIT licence. Montserrat font (SIL Open Font License), [Lucide](https://lucide.dev) icons (ISC).
