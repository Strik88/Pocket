"""Decide which client a recording belongs to: rules first, Claude as fallback."""

from __future__ import annotations

import logging
import re

from . import ai
from .config import Settings
from .pocket_api import Recording

log = logging.getLogger(__name__)


def _count(term: str, text: str) -> int:
    term = term.strip()
    if not term:
        return 0
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.I))


def by_rules(settings: Settings, rec: Recording) -> tuple[str | None, str]:
    """Returns (client, reason). Order: Pocket tag > keyword in title > keywords in summary/transcript."""
    rec_tags = {t.lower() for t in rec.tags}
    for c in settings.clients:
        hits = [t for t in c.pocket_tags if t.lower() in rec_tags]
        if hits:
            return c.name, f"Pocket-tag: {hits[0]}"

    for c in settings.clients:
        for term in [c.name, *c.keywords]:
            if _count(term, rec.title):
                return c.name, f"titel / title: {term}"

    body = f"{rec.summary}\n{rec.plain_transcript()}"
    scores: list[tuple[int, str, str]] = []
    for c in settings.clients:
        total, best = 0, ""
        for term in [c.name, *c.keywords]:
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


def classify(settings: Settings, rec: Recording) -> tuple[str | None, str]:
    """Returns (client or None, source) where source explains how it was decided."""
    client, reason = by_rules(settings, rec)
    if client:
        return client, f"rule: {reason}"
    if settings.ai_classify and settings.ai_ready and rec.has_transcript:
        try:
            client, reason = ai.classify(settings, rec)
            if client:
                return client, f"claude: {reason}"
        except ai.AIError as exc:
            log.warning("AI classification failed: %s", exc)
    return None, "unsorted"
