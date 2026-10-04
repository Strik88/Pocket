"""Decide which client (and project) a recording belongs to: rules first, Claude as fallback.

Order for the client:
  1. Pocket tag configured for a client
  2. Calendar meeting: attendee e-mail domain, or client name/keyword in the meeting title
  3. Client name/keyword in the recording title
  4. Keywords at least N times in summary + transcript (clear winner)
  5. Claude (optional), given the meeting details too
Then, within the client, a project whose keywords appear in meeting title, title or content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import ai
from .config import Client, Settings
from .pocket_api import Recording

log = logging.getLogger(__name__)


@dataclass
class Decision:
    client: str | None
    project: str | None
    source: str


def _count(term: str, text: str) -> int:
    term = term.strip()
    if not term:
        return 0
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.I))


def _terms(c: Client) -> list[str]:
    return [c.name, *c.keywords]


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower().strip() if "@" in email else ""


def by_rules(settings: Settings, rec: Recording, meeting=None) -> tuple[str | None, str]:
    """Returns (client, reason)."""
    rec_tags = {t.lower() for t in rec.tags}
    for c in settings.clients:
        hits = [t for t in c.pocket_tags if t.lower() in rec_tags]
        if hits:
            return c.name, f"Pocket-tag: {hits[0]}"

    if meeting is not None:
        domains = {_domain(e) for e in meeting.emails}
        for c in settings.clients:
            hit = next((d for d in c.email_domains if d.lower().lstrip("@") in domains), None)
            if hit:
                return c.name, f"agenda / calendar: {hit}"
        for c in settings.clients:
            for term in _terms(c):
                if _count(term, meeting.title) or any(_count(term, a) for a in meeting.attendees):
                    return c.name, f"agenda / calendar: {term}"

    for c in settings.clients:
        for term in _terms(c):
            if _count(term, rec.title):
                return c.name, f"titel / title: {term}"

    body = f"{rec.summary}\n{rec.plain_transcript()}"
    scores: list[tuple[int, str, str]] = []
    for c in settings.clients:
        total, best = 0, ""
        for term in _terms(c):
            n = _count(term, body)
            if n:
                total += n
                best = best or term
        if total:
            scores.append((total, c.name, best))
    scores.sort(reverse=True)
    if scores and scores[0][0] >= settings.keyword_min_hits:
        # Require a clear winner when several clients are mentioned
        if len(scores) == 1 or scores[0][0] >= 2 * scores[1][0]:
            return scores[0][1], f"trefwoord / keyword: {scores[0][2]} ({scores[0][0]}x)"
    return None, ""


def pick_project(settings: Settings, client: str, rec: Recording, meeting=None) -> str | None:
    cfg = settings.find_client(client)
    if not cfg or not cfg.projects:
        return None
    headline = f"{rec.title}\n{meeting.title if meeting else ''}"
    for p in cfg.projects:
        if any(_count(term, headline) for term in [p.name, *p.keywords]):
            return p.name
    body = f"{rec.summary}\n{rec.plain_transcript()}"
    scores = sorted(
        ((sum(_count(term, body) for term in [p.name, *p.keywords]), p.name) for p in cfg.projects),
        reverse=True,
    )
    if scores and scores[0][0] >= settings.keyword_min_hits and (len(scores) == 1 or scores[0][0] > scores[1][0]):
        return scores[0][1]
    return None


def classify(settings: Settings, rec: Recording, meeting=None) -> Decision:
    client, reason = by_rules(settings, rec, meeting)
    source = f"rule: {reason}" if client else "unsorted"
    if not client and settings.ai_classify and settings.ai_ready and rec.has_transcript:
        try:
            client, reason = ai.classify(settings, rec, meeting)
            if client:
                source = f"claude: {reason}"
        except ai.AIError as exc:
            log.warning("AI classification failed: %s", exc)
    project = pick_project(settings, client, rec, meeting) if client else None
    return Decision(client, project, source)
