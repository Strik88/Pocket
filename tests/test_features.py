"""Tests for the v0.2 features: calendar, projects, speakers, action items, reports,
keyword suggestions, search on meaning and autostart."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

from pocket_bridge import ai, autostart, meetings, reports, semantic, sync
from pocket_bridge.config import Client, Project, load_settings, save_settings
from pocket_bridge.dossier import status_path
from pocket_bridge.index import Index
from pocket_bridge.storage import location_from_path, parse_markdown, speakers_in

from .conftest import RECORDINGS, make_transport

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTIMEZONE
TZID:Europe/Amsterdam
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:1
SUMMARY:Overleg nieuw systeem
DTSTART;TZID=Europe/Amsterdam:20260903T100000
DTEND;TZID=Europe/Amsterdam:20260903T110000
ORGANIZER;CN=Ian:mailto:ian@example.com
ATTENDEE;CN=Petra Jansen:mailto:petra@gammagroep.nl
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Wekelijkse sync
DTSTART:20260801T080000Z
DTEND:20260801T083000Z
RRULE:FREQ=WEEKLY
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Vakantie
DTSTART;VALUE=DATE:20260903
DTEND;VALUE=DATE:20260904
END:VEVENT
END:VCALENDAR
"""


def ics_transport():
    return httpx.MockTransport(lambda r: httpx.Response(200, content=ICS))


@pytest.fixture
def calendar(settings, monkeypatch):
    settings.calendar_urls = ["webcal://cal.example.com/secret.ics"]
    save_settings(settings)
    real_fetch = meetings.fetch
    monkeypatch.setattr(meetings, "fetch", lambda s, url, force=False, transport=None: real_fetch(s, url, force, ics_transport()))
    return settings


# -- Calendar -------------------------------------------------------------------------


def test_parse_events_handles_timezones_recurrence_and_allday():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    events = meetings.parse_events(ICS, start, start + timedelta(days=10))
    titles = sorted(e.title for e in events)
    assert "Overleg nieuw systeem" in titles
    assert titles.count("Wekelijkse sync") == 1  # weekly from Sat Aug 1: only Sep 5 falls in Sep 1-11
    assert "Vakantie" not in titles  # all-day ignored
    ev = next(e for e in events if e.title == "Overleg nieuw systeem")
    assert ev.start.astimezone(timezone.utc).hour == 8  # 10:00 CEST
    assert "petra@gammagroep.nl" in ev.emails and any("Petra Jansen" in a for a in ev.attendees)


def test_match_event_and_classify_by_domain(calendar):
    calendar.clients.append(Client(name="Gamma", email_domains=["gammagroep.nl"]))
    save_settings(calendar)
    rec_at = datetime(2026, 9, 3, 8, 5, tzinfo=timezone.utc)
    ev = meetings.match_event(calendar, rec_at, 1800)
    assert ev and ev.title == "Overleg nieuw systeem"
    assert meetings.match_event(calendar, rec_at + timedelta(hours=5), 600) is None

    recs = {"rec_cal": {**RECORDINGS["rec_misc"], "id": "rec_cal", "title": "Opname 12", "recording_at": "2026-09-03T08:05:00Z"}}
    sync.run_sync(load_settings(), transport=make_transport(recs))
    path = next(calendar.root.glob("Klanten/Gamma/2026/*.md"))
    meta = parse_markdown(path).meta
    assert meta["meeting"] == "Overleg nieuw systeem"
    assert "agenda" in meta["client_source"]
    assert "Petra Jansen" in path.read_text()


def test_calendar_test_urls(calendar):
    res = meetings.test_urls(calendar)
    assert res[0]["ok"] and res[0]["events"] >= 0


# -- Projects -------------------------------------------------------------------------


def test_projects_folder_and_assign(settings):
    acme = settings.find_client("Acme")
    acme.projects = [Project(name="Q4 Planning", keywords=["Q4"])]  # 2x in summary + transcript
    save_settings(settings)
    sync.run_sync(load_settings(), transport=make_transport())
    path = next(settings.root.glob("Klanten/Acme/Q4 Planning/2026/*.md"))
    assert location_from_path(load_settings(), path) == ("Acme", "Q4 Planning")
    idx = Index(load_settings())
    assert idx.get("rec_acme").project == "Q4 Planning"
    idx.close()
    dossier = (settings.root / "Klanten/Acme/_Dossier.md").read_text()
    assert "### Q4 Planning" in dossier

    new = sync.assign(load_settings(), "rec_misc", "Acme", "Intern")
    assert new.as_posix().endswith("Klanten/Acme/Intern/2026/" + new.name)
    assert parse_markdown(new).meta["project"] == "Intern"
    assert "**Project:** Intern" in new.read_text()
    assert load_settings().find_client("Acme").find_project("intern")
    back = sync.assign(load_settings(), "rec_misc", "Acme", "")
    assert "/Intern/" not in back.as_posix() and "**Project:**" not in back.read_text()


# -- Speakers -------------------------------------------------------------------------


def test_rename_speakers_survives_resync(settings):
    sync.run_sync(settings, transport=make_transport())
    assert sync.rename_speakers(load_settings(), "rec_beta", {"A": "Anna", "B": "Bob"}) == 2
    path = next(settings.root.glob("Klanten/Betafabriek/2026/*.md"))
    assert speakers_in(path.read_text()) == ["Anna", "Bob"]
    sync.rename_speakers(load_settings(), "rec_beta", {"Anna": "Anna de Wit"})
    recs = dict(RECORDINGS)
    recs["rec_beta"] = {**RECORDINGS["rec_beta"], "updated_at": "2026-09-09T00:00:00Z"}
    sync.run_sync(load_settings(), full=True, transport=make_transport(recs))
    assert speakers_in(path.read_text()) == ["Anna de Wit", "Bob"]


def test_guess_speakers_uses_claude(settings, monkeypatch):
    sync.run_sync(settings, transport=make_transport())
    monkeypatch.setattr(ai, "suggest_speakers", lambda s, text, labels, att, owner: {labels[0]: "Anna"})
    idx = Index(load_settings())
    assert reports.guess_speakers(load_settings(), idx, "rec_beta") == {"A": "Anna"}
    idx.close()


# -- Action items -----------------------------------------------------------------------


def test_actions_toggle_persist_and_dossier_carry_back(settings):
    sync.run_sync(settings, transport=make_transport())
    s = load_settings()
    idx = Index(s)
    items = reports.list_actions(s, idx)
    assert {a["text"] for a in items} == {"Offerte sturen (Ian)", "Demo plannen"}
    assert reports.set_action(s, idx, "rec_acme", "Offerte sturen (Ian)", True)
    assert {a["text"] for a in reports.list_actions(s, idx)} == {"Demo plannen"}
    assert len(reports.list_actions(s, idx, include_done=True)) == 2
    idx.close()
    # Ticked items stay ticked when Pocket sends an update
    recs = dict(RECORDINGS)
    recs["rec_acme"] = {**RECORDINGS["rec_acme"], "updated_at": "2026-09-09T00:00:00Z"}
    sync.run_sync(load_settings(), full=True, transport=make_transport(recs))
    path = next(settings.root.glob("Klanten/Acme/2026/*.md"))
    assert parse_markdown(path).action_items == [(True, "Offerte sturen (Ian)")]
    # Ticking in the dossier is carried back to the conversation file
    dossier = settings.root / "Klanten/Betafabriek/_Dossier.md"
    dossier.write_text(dossier.read_text().replace("- [ ] Demo plannen", "- [x] Demo plannen"))
    sync.rebuild(load_settings())
    beta = next(settings.root.glob("Klanten/Betafabriek/2026/*.md"))
    assert parse_markdown(beta).action_items == [(True, "Demo plannen")]
    assert "- [ ] Demo plannen" not in dossier.read_text()


# -- Reports ----------------------------------------------------------------------------


def test_weekly_overview_without_ai(settings):
    sync.run_sync(settings, transport=make_transport())
    s = load_settings()
    idx = Index(s)
    path, text = reports.weekly_overview(s, idx, "2026-W36", with_ai=False)  # 31 Aug - 6 Sep
    idx.close()
    assert path.name == "2026-W36.md" and path.parent.name == "_Weekoverzichten"
    assert "## Acme" in text and "## Betafabriek" in text and "Offerte sturen" in text
    assert "Planning Q4" in text
    # Generated files are not indexed as recordings
    idx = Index(s)
    assert len(idx.list(limit=100)) == 3
    idx.close()


def test_week_parsing():
    assert reports.week_bounds("2026-W36")[0] == "2026-W36"
    assert reports.week_bounds("2026W36")[0] == "2026-W36"
    assert reports.week_bounds("w36")[0].endswith("-W36")
    with pytest.raises(ValueError):
        reports.week_bounds("volgende week")


def test_weekly_with_ai_review(settings, monkeypatch):
    sync.run_sync(settings, transport=make_transport())
    settings = load_settings()
    settings.anthropic_api_key = "sk-test"
    monkeypatch.setattr(ai, "weekly_summary", lambda s, md: "## Acme\nDrukke week met Acme.")
    idx = Index(settings)
    _, text = reports.weekly_overview(settings, idx, "2026-W36")
    idx.close()
    assert "## Terugblik" in text and "Drukke week" in text
    assert "#### Acme" in text  # Claude's headings sit below ours


def test_auto_weekly_writes_missed_week(settings, monkeypatch):
    sync.run_sync(settings, transport=make_transport())
    monkeypatch.setattr(reports, "week_bounds", _fixed_weeks)
    written = reports.auto_weekly(load_settings(), now=datetime(2026, 9, 9, 9, 0))  # Wednesday, week after
    assert written and written.name == "2026-W36.md"
    assert reports.auto_weekly(load_settings(), now=datetime(2026, 9, 9, 9, 0)) is None  # only once


def _fixed_weeks(week=""):
    from datetime import date

    if not week:
        week = "2026-W37"
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return week, monday, monday + timedelta(days=6)


def test_briefing_and_followup(settings, monkeypatch):
    sync.run_sync(settings, transport=make_transport())
    seen = {}

    def fake_briefing(s, client, dossier, transcripts):
        seen["client"], seen["n"] = client, len(transcripts)
        return "## Situatie\n- alles loopt"

    monkeypatch.setattr(ai, "briefing", fake_briefing)
    monkeypatch.setattr(ai, "followup", lambda s, md, att, sender="": ai.FollowUp(subject="Bedankt", body="Hoi Jan,\n\nDank!"))
    s = load_settings()
    idx = Index(s)
    path, text = reports.make_briefing(s, idx, "acme")
    assert seen == {"client": "Acme", "n": 1} and path.parent.name == "_Briefings" and "Situatie" in path.read_text()
    mail = reports.make_followup(s, idx, "rec_acme")
    idx.close()
    assert mail["subject"] == "Bedankt" and Path(mail["path"]).exists()


def test_client_status_in_dossier(settings, monkeypatch):
    settings.anthropic_api_key = "sk-test"
    save_settings(settings)
    monkeypatch.setattr(ai, "client_status", lambda s, c, t, previous="": f"- Fase: offerte ({c})")
    sync.run_sync(load_settings(), transport=make_transport())
    assert status_path(load_settings(), "Acme").exists()
    assert "Fase: offerte (Acme)" in (settings.root / "Klanten/Acme/_Dossier.md").read_text()


def test_suggest_keywords_after_move(settings):
    recs = dict(RECORDINGS)
    recs["rec_misc"] = {
        **RECORDINGS["rec_misc"],
        "transcript": "We spraken met Karel over het project Zonnepark. Volgende week belt Karel terug over Zonnepark en de vergunning.",
    }
    sync.run_sync(settings, transport=make_transport(recs))
    s = load_settings()
    sync.assign(s, "rec_misc", "Acme")
    idx = Index(load_settings())
    sugg = reports.suggest_keywords(load_settings(), idx, "rec_misc", "Acme")
    idx.close()
    assert "Karel" in sugg and "Zonnepark" in sugg
    assert "Losse" not in sugg  # first title word is not a name
    reports.add_keywords(load_settings(), "Acme", ["Karel"])
    assert "Karel" in load_settings().find_client("Acme").keywords


# -- Search on meaning -------------------------------------------------------------------


class FakeEmbedder:
    """Maps words to concept axes, so 'factuur' and 'facturatie' and 'betaling' land close together."""

    name = "fake-model"
    CONCEPTS = {"factu": 0, "betal": 0, "geld": 0, "planning": 1, "q4": 1, "boodschap": 2}

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(4, dtype=np.float32)
            low = t.lower()
            for key, axis in self.CONCEPTS.items():
                v[axis] += low.count(key)
            v[3] = 0.01
            out.append(v)
        return semantic._normalise(np.array(out))


def test_semantic_search_finds_by_meaning(settings):
    semantic.set_embedder(FakeEmbedder())
    try:
        settings.semantic_search = True
        save_settings(settings)
        sync.run_sync(load_settings(), transport=make_transport())
        s = load_settings()
        idx = Index(s)
        st = semantic.status(s, idx)
        assert st["indexed"] == 3 and st["chunks"] >= 3
        hits = semantic.search(s, idx, "betalingen en geld")
        assert hits[0]["pocket_id"] == "rec_beta"
        # Keyword search alone finds nothing for this query; hybrid does
        assert idx.search("betalingen") == []
        assert semantic.hybrid_search(s, idx, "betalingen")[0]["pocket_id"] == "rec_beta"
        # Nothing to do on a second run
        assert semantic.update(s, idx) == 0
        idx.close()
    finally:
        semantic.set_embedder(None)


def test_chunking_overlaps_and_covers_text():
    transcript = "\n".join(f"Zin nummer {i} met wat extra woorden erbij." for i in range(200))
    chunks = semantic.chunk_text("Titel", "Samenvatting", transcript)
    assert chunks[0].startswith("Titel")
    assert all(len(c) <= semantic.CHUNK_CHARS * 1.5 for c in chunks)
    assert "Zin nummer 199" in chunks[-1]


# -- Autostart ------------------------------------------------------------------------------


def test_autostart_linux_desktop_file(tmp_path, monkeypatch):
    if autostart.sys.platform in ("darwin", "win32"):
        pytest.skip("linux only")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.is_enabled()
    assert autostart.set_enabled(True)
    text = (tmp_path / "autostart" / "pocket-bridge.desktop").read_text()
    assert "pocket_bridge tray --no-browser" in text
    assert not autostart.set_enabled(False)


def test_autostart_mac_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart.Path, "home", lambda: tmp_path)
    autostart.enable()
    import plistlib

    data = plistlib.loads((tmp_path / "Library/LaunchAgents/nl.pocketbridge.app.plist").read_bytes())
    assert data["RunAtLoad"] is True and data["ProgramArguments"][-3:] == ["pocket_bridge", "tray", "--no-browser"][-3:]
    assert autostart.is_enabled()
    autostart.disable()
    assert not autostart.is_enabled()



def test_no_console_streams_are_replaced(tmp_path, monkeypatch):
    import sys

    from pocket_bridge import __main__ as cli

    monkeypatch.setenv("POCKET_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    cli._ensure_streams()
    assert sys.stdout is not None and sys.stderr is not None
    print("hello from pythonw")
    sys.stdout.flush()
    assert "hello from pythonw" in (tmp_path / "pocket-bridge.log").read_text()
