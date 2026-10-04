"""Per-client dossier: an auto-maintained overview file in each client folder."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import Settings
from .i18n import t
from .storage import client_dir, parse_markdown

DOSSIER_NAME = "_Dossier.md"


def _existing_notes(path: Path, lang: str) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(t(lang, 'notes'))}\s*$", text, flags=re.M)
    return text[m.end():].strip() if m else ""


def build_dossier(settings: Settings, client: str, rows: list) -> Path:
    """rows: index rows for this client (any order)."""
    lang = settings.language
    folder = client_dir(settings, client)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DOSSIER_NAME
    rows = sorted(rows, key=lambda r: r.date or "", reverse=True)

    open_items: list[str] = []
    for r in rows:
        parsed = parse_markdown(Path(r.path))
        if not parsed:
            continue
        for done, text in parsed.action_items:
            if not done:
                open_items.append(f"- [ ] {text} — [[{Path(r.path).stem}]]")

    client_cfg = settings.find_client(client)
    lines = [
        "---",
        f'type: "client-dossier"',
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
    if client_cfg and client_cfg.keywords:
        lines.append(f"- **Keywords:** {', '.join(client_cfg.keywords)}")
    if client_cfg and client_cfg.notes:
        lines.append(f"- **Info:** {client_cfg.notes}")
    lines += ["", f"## {t(lang, 'open_actions')}", ""]
    lines += open_items or [t(lang, "none")]
    lines += ["", f"## {t(lang, 'all_conversations')}", ""]
    for r in rows:
        summary = (r.summary or "").strip().splitlines()
        first = next((s.strip("#*- ").strip() for s in summary if s.strip("#*- ").strip()), "")
        lines.append(f"- {(r.date or '')[:10]} — [[{Path(r.path).stem}]]" + (f": {first[:160]}" if first else ""))
    lines += ["", f"## {t(lang, 'notes')}", "", _existing_notes(path, lang), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def rebuild_all(settings: Settings, index) -> int:
    by_client: dict[str, list] = {}
    for r in index.list(limit=100_000):
        if r.client:
            by_client.setdefault(r.client, []).append(r)
    for client, rows in by_client.items():
        build_dossier(settings, client, rows)
    return len(by_client)
