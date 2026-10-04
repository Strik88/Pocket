"""Match recordings to calendar meetings via private iCal links (Google Calendar, Outlook).

Google Calendar: Settings -> your calendar -> "Secret address in iCal format".
Outlook: Settings -> Calendar -> Shared calendars -> Publish a calendar -> ICS link.
No login needed; the link is cached locally for 30 minutes.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import icalendar
import recurring_ical_events

from .config import Settings
from .storage import meta_dir

log = logging.getLogger(__name__)
CACHE_SECONDS = 30 * 60


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)  # "Name <email>" or email
    emails: list[str] = field(default_factory=list)
    location: str = ""

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
            "attendees": self.attendees,
            "location": self.location,
        }


def _normalise_url(url: str) -> str:
    url = url.strip()
    return "https://" + url[len("webcal://") :] if url.lower().startswith("webcal://") else url


def _cache_file(settings: Settings, url: str) -> Path:
    d = meta_dir(settings) / "calendar"
    d.mkdir(exist_ok=True)
    return d / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".ics")


def fetch(settings: Settings, url: str, force: bool = False, transport: httpx.BaseTransport | None = None) -> bytes:
    cache = _cache_file(settings, url)
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SECONDS:
        return cache.read_bytes()
    try:
        with httpx.Client(timeout=30, follow_redirects=True, transport=transport) as http:
            resp = http.get(_normalise_url(url))
            resp.raise_for_status()
            data = resp.content
        if b"BEGIN:VCALENDAR" not in data[:2000]:
            raise ValueError("Dit is geen iCal-agenda / not an iCal calendar")
        cache.write_bytes(data)
        return data
    except Exception:
        if cache.exists():  # offline: use the last copy
            log.warning("calendar fetch failed, using cached copy", exc_info=True)
            return cache.read_bytes()
        raise


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()  # floating time = local time
    return None  # all-day events (date) are ignored


def _person(prop) -> tuple[str, str]:
    email = str(prop).replace("mailto:", "").replace("MAILTO:", "").strip()
    name = ""
    params = getattr(prop, "params", {}) or {}
    if params.get("CN"):
        name = str(params["CN"]).strip()
    return name, email.lower() if "@" in email else ""


def parse_events(data: bytes, start: datetime, end: datetime) -> list[Event]:
    cal = icalendar.Calendar.from_ical(data)
    out = []
    for ve in recurring_ical_events.of(cal).between(start, end):
        s, e = _as_dt(ve.get("DTSTART").dt if ve.get("DTSTART") else None), None
        if not s:
            continue
        if ve.get("DTEND"):
            e = _as_dt(ve.get("DTEND").dt)
        elif ve.get("DURATION"):
            e = s + ve.get("DURATION").dt
        e = e or s + timedelta(hours=1)
        if str(ve.get("STATUS", "")).upper() == "CANCELLED":
            continue
        attendees, emails = [], []
        raw = ve.get("ATTENDEE") or []
        people = raw if isinstance(raw, list) else [raw]
        if ve.get("ORGANIZER"):
            people = [ve.get("ORGANIZER"), *people]
        for p in people:
            name, email = _person(p)
            if email and email in emails:
                continue
            if email:
                emails.append(email)
            label = f"{name} <{email}>" if name and email else (name or email)
            if label:
                attendees.append(label)
        out.append(
            Event(
                title=str(ve.get("SUMMARY") or "").strip(),
                start=s,
                end=e,
                attendees=attendees,
                emails=emails,
                location=str(ve.get("LOCATION") or "").strip(),
            )
        )
    return out


def events_around(settings: Settings, when: datetime, hours: float = 12, transport=None) -> list[Event]:
    events: list[Event] = []
    start, end = when - timedelta(hours=hours), when + timedelta(hours=hours)
    for url in settings.calendar_urls:
        if not url.strip():
            continue
        try:
            events += parse_events(fetch(settings, url, transport=transport), start, end)
        except Exception as exc:
            log.warning("calendar %s: %s", url[:40], exc)
    return events


def match_event(settings: Settings, recorded_at: datetime | None, duration_seconds: float | None, transport=None) -> Event | None:
    """The meeting that best overlaps the recording (within a margin)."""
    if not recorded_at or not settings.calendar_urls:
        return None
    rec_start = recorded_at.astimezone(timezone.utc)
    rec_end = rec_start + timedelta(seconds=duration_seconds or 30 * 60)
    margin = timedelta(minutes=settings.calendar_margin_minutes)
    best, best_overlap = None, timedelta(0)
    for ev in events_around(settings, rec_start, transport=transport):
        s, e = ev.start.astimezone(timezone.utc), ev.end.astimezone(timezone.utc)
        if (e - s) >= timedelta(hours=12):  # all-day-like blocks
            continue
        overlap = min(e + margin, rec_end) - max(s - margin, rec_start)
        if overlap > best_overlap:
            best, best_overlap = ev, overlap
    return best


def test_urls(settings: Settings, transport=None) -> list[dict]:
    """For the settings page: how many meetings each link has in the past two months and next month."""
    now = datetime.now(timezone.utc)
    results = []
    for url in settings.calendar_urls:
        try:
            events = parse_events(fetch(settings, url, force=True, transport=transport), now - timedelta(days=60), now + timedelta(days=30))
            results.append({"url": url[:60] + ("…" if len(url) > 60 else ""), "ok": True, "events": len(events)})
        except Exception as exc:
            results.append({"url": url[:60], "ok": False, "error": str(exc)})
    return results


__all__ = ["Event", "match_event", "events_around", "parse_events", "test_urls"]
