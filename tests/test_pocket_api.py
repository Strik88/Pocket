from pocket_bridge.pocket_api import PocketAuthError, PocketClient, parse_recording

from .conftest import RECORDINGS, make_transport


def test_parse_list_segments_and_v2_summary():
    rec = parse_recording(RECORDINGS["rec_acme"])
    assert rec.title == "Kickoff Acme"
    assert [s.speaker for s in rec.segments] == ["Ian", "Jan"]
    assert "Planning Q4" in rec.summary
    assert rec.action_items == ["Offerte sturen (Ian)"]
    assert rec.tags == ["werk"]
    assert rec.recorded_by == "Ian"
    assert rec.duration_seconds == 1800


def test_parse_dict_segments_and_list_summary():
    rec = parse_recording(RECORDINGS["rec_beta"])
    assert len(rec.segments) == 2
    assert rec.summary == "Facturatieproblemen bij Betafabriek."
    assert rec.action_items == ["Demo plannen"]


def test_parse_plain_string_and_missing_fields():
    rec = parse_recording(RECORDINGS["rec_misc"])
    assert rec.transcript_text.startswith("Ik moet")
    assert rec.summary == "" and rec.action_items == []
    assert not parse_recording(RECORDINGS["rec_pending"]).has_transcript
    empty = parse_recording({"id": 5})
    assert empty.id == "5" and empty.title == "Untitled"


def test_pagination_walks_all_pages():
    with PocketClient("pk_test", transport=make_transport()) as c:
        ids = [r["id"] for r in c.iter_recordings()]
    assert sorted(ids) == sorted(RECORDINGS)


def test_get_recording_unwraps_data():
    with PocketClient("pk_test", transport=make_transport()) as c:
        rec = c.get_recording("rec_acme")
    assert rec.id == "rec_acme" and rec.segments


def test_bad_key():
    with PocketClient("pk_wrong", transport=make_transport()) as c:
        try:
            c.check()
        except PocketAuthError:
            return
    raise AssertionError("expected PocketAuthError")


def test_check_returns_total():
    with PocketClient("pk_test", transport=make_transport()) as c:
        assert c.check() == len(RECORDINGS)
