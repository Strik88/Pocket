"""Workflow on top of the transcripts: action items, briefings, follow-ups,
weekly overviews, per-client status notes and keyword suggestions."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import ai
from .config import Settings
from .dossier import DOSSIER_NAME, build_dossier, read_status, status_path
from .i18n import t
from .index import Index, _STOP
from .storage import client_dir, demote_headings, parse_markdown, set_action_done, speakers_in

# -- Action items -------------------------------------------------------------------


def list_actions(settings: Settings, index: Index, client: str | None = None, include_done: bool = False, unsorted: bool = False) -> list[dict]:
    out = []
    rows = index.list(client=client or None, unsorted_only=unsorted, limit=100_000)
    for r in rows:
        parsed = parse_markdown(Path(r.path))
        if not parsed:
            continue
        for done, text in parsed.action_items:
            if done and not include_done:
                continue
            out.append(
                {
                    "pocket_id": r.pocket_id,
                    "client": r.client,
                    "project": r.project,
                    "title": r.title,
                    "date": (r.date or "")[:10],
                    "text": text,
                    "done": done,
                }
            )
    return out


def set_action(settings: Settings, index: Index, pocket_id: str, text: str, done: bool) -> bool:
    row = index.find(pocket_id)
    if not row:
        raise ValueError(f"recording not found: {pocket_id}")
    changed = set_action_done(Path(row.path), text, done)
    if changed:
        index.upsert_file(Path(row.path))
        if row.client:
            build_dossier(settings, row.client, index.list(client=row.client, limit=100_000))
    return changed


# -- Recent context per client ------------------------------------------------------------


def _recent(index: Index, client: str, n: int) -> list[tuple[str, str]]:
    rows = index.list(client=client, limit=n)
    return [(f"{r.title} ({(r.date or '')[:10]})", Path(r.path).read_text(encoding="utf-8")) for r in rows]


def _dossier_text(settings: Settings, client: str) -> str:
    p = client_dir(settings, client) / DOSSIER_NAME
    return p.read_text(encoding="utf-8") if p.exists() else ""


# -- Briefing -----------------------------------------------------------------------


def make_briefing(settings: Settings, index: Index, client: str, last_n: int = 3) -> tuple[Path, str]:
    cfg = settings.find_client(client)
    client = cfg.name if cfg else client
    text = ai.briefing(settings, client, _dossier_text(settings, client), _recent(index, client, last_n))
    folder = client_dir(settings, client) / t(settings.language, "briefings_dir")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d %H%M} {t(settings.language, 'briefing')}.md"
    path.write_text(f"# {t(settings.language, 'briefing')}: {client}\n\n_{datetime.now():%Y-%m-%d %H:%M}_\n\n{text}\n", encoding="utf-8")
    return path, text


# -- Follow-up mail -----------------------------------------------------------------


def make_followup(settings: Settings, index: Index, ref: str, sender: str = "") -> dict:
    row = index.find(ref)
    if not row:
        raise ValueError(f"recording not found: {ref}")
    path = Path(row.path)
    parsed = parse_markdown(path)
    attendees = list(parsed.meta.get("attendees") or []) if parsed else []
    result = ai.followup(settings, path.read_text(encoding="utf-8"), attendees, sender)
    if not result:
        raise ai.AIError("Claude gaf geen mail terug / no e-mail returned")
    emails = [m.group(0) for a in attendees for m in [re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", a)] if m]
    folder = client_dir(settings, row.client, None) / t(settings.language, "followups_dir") if row.client else client_dir(settings, None) / t(settings.language, "followups_dir")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{path.stem}.md"
    out.write_text(
        f"# {t(settings.language, 'followup')}: {row.title}\n\n"
        f"**To:** {', '.join(emails) or '-'}\n\n**Subject:** {result.subject}\n\n---\n\n{result.body}\n",
        encoding="utf-8",
    )
    return {"to": emails, "subject": result.subject, "body": result.body, "path": str(out)}


# -- Weekly overview ----------------------------------------------------------------


def week_bounds(week: str = "") -> tuple[str, date, date]:
    """week like '2026-W40' (empty = current week). Returns (label, monday, sunday)."""
    m = re.fullmatch(r"\s*(?:(\d{4})\s*-?\s*)?W?\s*(\d{1,2})\s*", week or "", flags=re.I)
    if week and not m:
        raise ValueError(f"Onbekende week / unknown week: {week} (bijv. 2026-W40)")
    if m:
        y = int(m.group(1) or date.today().isocalendar()[0])
        monday = date.fromisocalendar(y, int(m.group(2)), 1)
    else:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", monday, monday + timedelta(days=6)


def weekly_path(settings: Settings, label: str) -> Path:
    return settings.root / t(settings.language, "weekly_dir") / f"{label}.md"


def weekly_overview(settings: Settings, index: Index, week: str = "", with_ai: bool = True) -> tuple[Path, str]:
    lang = settings.language
    label, monday, sunday = week_bounds(week)
    rows = index.list(since=monday.isoformat(), until=sunday.isoformat(), limit=100_000)
    rows.sort(key=lambda r: (r.client or "~", r.date or ""))
    lines = [f"# {t(lang, 'weekly_title', week=label)}", "", f"_{monday:%d-%m-%Y} – {sunday:%d-%m-%Y}_", ""]
    body: list[str] = []
    if not rows:
        body.append(t(lang, "weekly_empty"))
    current = object()
    for r in rows:
        if r.client != current:
            current = r.client
            body += ["", f"## {r.client or t(lang, 'unsorted')}", ""]
        parsed = parse_markdown(Path(r.path))
        body.append(f"### {(r.date or '')[:10]} — [[{Path(r.path).stem}]]" + (f" ({r.project})" if r.project else ""))
        if parsed and parsed.summary:
            body += ["", parsed.summary.strip()]
        open_items = [a for done, a in (parsed.action_items if parsed else []) if not done]
        if open_items:
            body += ["", f"**{t(lang, 'decisions_actions')}:**"] + [f"- [ ] {a}" for a in open_items]
        body.append("")
    review = ""
    if with_ai and rows and settings.ai_ready:
        try:
            review = ai.weekly_summary(settings, "\n".join(body))
        except ai.AIError:
            review = ""
    if review:
        lines += [f"## {t(lang, 'weekly_review')}", "", demote_headings(review.strip()), ""]
    text = "\n".join(lines + body).strip() + "\n"
    path = weekly_path(settings, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, text


def auto_weekly(settings: Settings, now: datetime | None = None) -> Path | None:
    """From Friday 16:00, write this week's overview once (and last week's if it was missed)."""
    now = now or datetime.now()
    index = Index(settings)
    try:
        index.refresh()
        written = None
        label, monday, _ = week_bounds()
        if (now.weekday() == 4 and now.hour >= 16) or now.weekday() >= 5:
            if not weekly_path(settings, label).exists():
                written, _ = weekly_overview(settings, index)
        prev_label, prev_monday, prev_sunday = week_bounds(f"{(monday - timedelta(days=7)).isocalendar()[0]}-W{(monday - timedelta(days=7)).isocalendar()[1]:02d}")
        if not weekly_path(settings, prev_label).exists() and index.list(since=prev_monday.isoformat(), until=prev_sunday.isoformat(), limit=1):
            written, _ = weekly_overview(settings, index, prev_label)
        return written
    finally:
        index.close()


# -- Client status (Claude) ---------------------------------------------------------------


def update_client_status(settings: Settings, index: Index, client: str, last_n: int = 3) -> str:
    previous, _ = read_status(settings, client)
    text = ai.client_status(settings, client, _recent(index, client, last_n), previous)
    if text:
        status_path(settings, client).write_text(text.strip() + "\n", encoding="utf-8")
    return text


# -- Speakers ---------------------------------------------------------------------


def speaker_labels(settings: Settings, index: Index, ref: str) -> list[str]:
    row = index.find(ref)
    return speakers_in(Path(row.path).read_text(encoding="utf-8")) if row else []


def guess_speakers(settings: Settings, index: Index, ref: str) -> dict[str, str]:
    row = index.find(ref)
    if not row:
        raise ValueError(f"recording not found: {ref}")
    text = Path(row.path).read_text(encoding="utf-8")
    parsed = parse_markdown(Path(row.path))
    attendees = list(parsed.meta.get("attendees") or []) if parsed else []
    owner = str(parsed.meta.get("recorded_by") or "") if parsed else ""
    return ai.suggest_speakers(settings, text, speakers_in(text), attendees, owner)


# -- Learning keywords from manual moves ------------------------------------------------------

_CAP_MID = re.compile(r"(?<=[a-zà-ÿ0-9,;:] )([A-ZÀ-Þ][\w&'-]{2,}(?: [A-ZÀ-Þ][\w&'-]{2,})?)")


def suggest_keywords(settings: Settings, index: Index, ref: str, client: str, limit: int = 5) -> list[str]:
    """Names that are typical for this recording but not yet known as keywords.

    Looks at capitalised words in the middle of sentences (names, companies,
    projects) and prefers terms that don't appear in other clients' recordings.
    """
    row = index.find(ref)
    cfg = settings.find_client(client)
    if not row or not cfg:
        return []
    parsed = parse_markdown(Path(row.path))
    if not parsed:
        return []
    text = f"{parsed.title}. {parsed.summary}\n{parsed.transcript}"
    known = {k.lower() for c in settings.clients for k in [c.name, *c.keywords]}
    speakers = {s.lower() for s in speakers_in(Path(row.path).read_text(encoding="utf-8"))}
    counts = Counter(m.group(1).strip() for m in _CAP_MID.finditer(text))
    for word in re.findall(r"[\w&'-]{3,}", parsed.title)[1:]:  # first word is capitalised anyway
        if word[0].isupper():
            counts[word] += 2
    scored = []
    for term, n in counts.items():
        low = term.lower()
        if low in known or low in speakers or low in _STOP or re.match(r"^(speaker|spreker)\b", low) or n < 2:
            continue
        others = [h for h in index.search(f'"{term}"', limit=20) if h["client"] and h["client"] != cfg.name]
        scored.append((n / (1 + 2 * len(others)), term))
    scored.sort(reverse=True)
    return [term for _, term in scored[:limit]]


def add_keywords(settings: Settings, client: str, keywords: list[str]) -> None:
    from .config import save_settings

    cfg = settings.find_client(client)
    if not cfg:
        return
    for k in keywords:
        if k.strip() and k.strip().lower() not in {x.lower() for x in cfg.keywords}:
            cfg.keywords.append(k.strip())
    save_settings(settings)


__all__ = [
    "list_actions", "set_action", "make_briefing", "make_followup", "weekly_overview", "auto_weekly",
    "update_client_status", "speaker_labels", "guess_speakers", "suggest_keywords", "add_keywords",
]
