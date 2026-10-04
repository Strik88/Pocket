"""Per-client dossier: an auto-maintained overview file in each client folder.

Ticking an action item in the dossier is carried back to the conversation file
the next time the dossier is rebuilt, so you can work from either place.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import Settings
from .i18n import t
from .storage import ACTION_LINE, client_dir, meta_dir, parse_markdown, safe_name, set_action_done

DOSSIER_NAME = "_Dossier.md"
_LINKED = re.compile(r"^(?P<text>.+) — \[\[(?P<stem>[^\]]+)\]\]$")


def status_path(settings: Settings, client: str) -> Path:
    d = meta_dir(settings) / "status"
    d.mkdir(exist_ok=True)
    return d / f"{safe_name(client)}.md"


def read_status(settings: Settings, client: str) -> tuple[str, str]:
    """(status markdown, updated timestamp) as written by Claude, or ("", "")."""
    p = status_path(settings, client)
    if not p.exists():
        return "", ""
    return p.read_text(encoding="utf-8"), datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _existing_notes(text: str, lang: str) -> str:
    m = re.search(rf"^## {re.escape(t(lang, 'notes'))}\s*$", text, flags=re.M)
    return text[m.end():].strip() if m else ""


def _carry_back_ticks(text: str, rows: list) -> None:
    """Action items ticked in the dossier -> tick them in the conversation file."""
    by_stem = {Path(r.path).stem: Path(r.path) for r in rows}
    for line in text.splitlines():
        m = ACTION_LINE.match(line)
        if not m or m.group("mark").lower() != "x":
            continue
        linked = _LINKED.match(m.group("text").strip())
        if linked and linked.group("stem") in by_stem:
            set_action_done(by_stem[linked.group("stem")], linked.group("text"), True)


def build_dossier(settings: Settings, client: str, rows: list) -> Path:
    """rows: index rows for this client (any order)."""
    lang = settings.language
    folder = client_dir(settings, client)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DOSSIER_NAME
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old:
        _carry_back_ticks(old, rows)
    rows = sorted(rows, key=lambda r: r.date or "", reverse=True)

    open_items: list[str] = []
    for r in rows:
        parsed = parse_markdown(Path(r.path))
        if not parsed:
            continue
        for done, text in parsed.action_items:
            if not done:
                open_items.append(f"- [ ] {text} — [[{Path(r.path).stem}]]")

    cfg = settings.find_client(client)
    lines = [
        "---",
        'type: "client-dossier"',
        f'client: "{client}"',
        f'updated: "{datetime.now().isoformat(timespec="minutes")}"',
        "---",
        f"# {t(lang, 'dossier')}: {client}",
        "",
        f"_{t(lang, 'dossier_note')}_",
        "",
        f"- **{t(lang, 'conversations')}:** {len(rows)}",
        f"- **{t(lang, 'last_contact')}:** {(rows[0].date or '')[:10] if rows else '-'}",
    ]
    if cfg and cfg.keywords:
        lines.append(f"- **Keywords:** {', '.join(cfg.keywords)}")
    if cfg and cfg.email_domains:
        lines.append(f"- **E-mail:** {', '.join(cfg.email_domains)}")
    if cfg and cfg.notes:
        lines.append(f"- **Info:** {cfg.notes}")

    status, updated = read_status(settings, client)
    if status:
        lines += ["", f"## {t(lang, 'status')}", "", f"_{t(lang, 'status_note', date=updated)}_", "", status.strip()]

    lines += ["", f"## {t(lang, 'open_actions')}", ""]
    lines += open_items or [t(lang, "none")]
    lines += ["", f"## {t(lang, 'all_conversations')}", ""]

    def conv_line(r) -> str:
        summary = (r.summary or "").strip().splitlines()
        first = next((s.strip("#*- ").strip() for s in summary if s.strip("#*- ").strip()), "")
        return f"- {(r.date or '')[:10]} — [[{Path(r.path).stem}]]" + (f": {first[:160]}" if first else "")

    projects = sorted({r.project for r in rows if r.project})
    if projects:
        for proj in projects:
            lines += [f"### {proj}", ""] + [conv_line(r) for r in rows if r.project == proj] + [""]
        general = [r for r in rows if not r.project]
        if general:
            lines += [f"### {t(lang, 'general')}", ""] + [conv_line(r) for r in general] + [""]
    else:
        lines += [conv_line(r) for r in rows]
    lines += ["", f"## {t(lang, 'notes')}", "", _existing_notes(old, lang), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def rebuild_all(settings: Settings, index) -> int:
    by_client: dict[str, list] = {}
    for r in index.list(limit=100_000):
        if r.client:
            by_client.setdefault(r.client, []).append(r)
    for client, rows in by_client.items():
        build_dossier(settings, client, rows)
    index.refresh()  # ticks carried back change files
    return len(by_client)
