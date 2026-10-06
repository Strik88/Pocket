"""Pocket's API has sent recordings in several shapes. These tests pin the ones seen in the wild,
plus the upgrade that rewrites existing files once the reading improves."""

import json
from pathlib import Path

from pocket_bridge import sync
from pocket_bridge.index import Index
from pocket_bridge.pocket_api import parse_recording
from pocket_bridge.storage import parse_markdown, speakers_in

from .conftest import make_transport

# The current shape of GET /public/recordings/{id}: action items nested twice under v2.
CURRENT = {
    "id": "rec_now",
    "title": "Reflective interview",
    "recording_at": "2026-09-25T13:35:00Z",
    "updated_at": "2026-09-25T14:20:00Z",
    "duration": 2400,
    "transcript": [
        {"speaker": "Speaker 1", "text": "Hoe kijk je terug op de stage?", "start": 0, "end": 3},
        {"speaker": "Speaker 2", "text": "Ik heb veel geleerd over data.", "start": 3, "end": 8},
    ],
    "summarizations": {
        "sum_old": {
            "processingStatus": "processing",
            "v2": {"summary": {"markdown": "## Half af"}, "actionItems": {"actionItems": [{"title": "Niet tonen"}]}},
        },
        "sum_456": {
            "processingStatus": "completed",
            "v2": {
                "summary": {"title": "Interview", "markdown": "## Kern\nTerugblik op de stage."},
                "actionItems": {
                    "actionItems": [
                        {"id": "a1", "title": "Verslag insturen", "dueDate": "2026-10-01T00:00:00Z", "status": "TODO", "isCompleted": False},
                        {"id": "a2", "title": "Beoordeling invullen", "status": "DONE", "isCompleted": True},
                    ]
                },
            },
        },
    },
}


def test_current_shape_action_items_and_completion():
    rec = parse_recording(CURRENT)
    assert rec.action_items == ["Verslag insturen (2026-10-01)", "Beoordeling invullen"]
    assert rec.actions_completed == {"Beoordeling invullen"}
    assert "Half af" not in rec.summary and "Terugblik" in rec.summary
    assert [s.speaker for s in rec.segments] == ["Speaker 1", "Speaker 2"]


def test_v2_action_items_entry_and_label_field():
    rec = parse_recording({
        "id": "r", "title": "x",
        "transcript": "Tekst.",
        "summarizations": {"v2_action_items": {"actionItems": [{"id": "1", "label": "Bel Petra"}]}},
    })
    assert rec.action_items == ["Bel Petra"]


def test_v2_action_items_entry_does_not_duplicate():
    rec = parse_recording(dict(CURRENT, summarizations={
        **CURRENT["summarizations"],
        "v2_action_items": {"actionItems": [{"id": "a1", "title": "Verslag insturen"}]},
    }))
    assert rec.action_items == ["Verslag insturen (2026-10-01)", "Beoordeling invullen"]


def test_long_plain_text_parses_fast():
    import time

    line = " ".join(["woord"] * 20_000)
    started = time.perf_counter()
    rec = parse_recording({"id": "r", "title": "x", "transcript": "\n".join([line] * 20)})
    assert time.perf_counter() - started < 2 and rec.segments == []


def test_segments_with_speakers_win_over_plain_text():
    rec = parse_recording({
        "id": "r", "title": "x",
        "transcript": "Hallo. Hoi.",
        "transcriptSegments": [
            {"text": "Hallo.", "start": 0.6, "end": 1.2, "speaker": "Alex"},
            {"text": "Hoi.", "start": 1.5, "end": 2.0, "speaker": "Unknown Speaker 2"},
        ],
    })
    assert [s.speaker for s in rec.segments] == ["Alex", "Unknown Speaker 2"]
    assert rec.transcript_text == ""


def test_numeric_and_id_speakers_become_readable():
    numbered = parse_recording({"id": "r", "title": "x", "transcript": [
        {"speaker": 0, "text": "Eerste."}, {"speaker": 1, "text": "Tweede."}, {"speaker": 0, "text": "Derde."},
    ]})
    assert [s.speaker for s in numbered.segments] == ["Speaker 1", "Speaker 2", "Speaker 1"]

    pyannote = parse_recording({"id": "r", "title": "x", "transcript": [
        {"speaker_label": "SPEAKER_00", "text": "Eerste."}, {"speaker_label": "SPEAKER_01", "text": "Tweede."},
    ]})
    assert [s.speaker for s in pyannote.segments] == ["Speaker 1", "Speaker 2"]

    named = parse_recording({
        "id": "r", "title": "x",
        "speakers": [{"id": "spk_a", "name": "Ian"}, {"id": "spk_b", "name": "Gaurav"}],
        "transcript": {"segments": [{"speakerId": "spk_a", "text": "Vraag."}, {"speakerId": "spk_b", "text": "Antwoord."}]},
    })
    assert [s.speaker for s in named.segments] == ["Ian", "Gaurav"]


def test_speaker_turns_recovered_from_plain_text():
    rec = parse_recording({"id": "r", "title": "x", "transcription": {"transcription": {"text": (
        "Speaker 1: Goedemorgen allemaal.\n"
        "Speaker 2: Goedemorgen, zullen we beginnen?\n"
        "dat lijkt me goed\n"
        "Speaker 1 (00:01:10): Ja, het eerste punt is de planning."
    )}}})
    assert [s.speaker for s in rec.segments] == ["Speaker 1", "Speaker 2", "Speaker 1"]
    assert rec.segments[1].text.endswith("dat lijkt me goed")
    assert rec.segments[2].start == 70


def test_prose_with_a_colon_is_left_alone():
    text = "\n".join([
        "Vandaag gaan we het hebben over regressie.",
        "Let op: dit komt terug op het tentamen.",
        "We beginnen met een voorbeeld uit de praktijk.",
        "Daarna kijken we naar de opdracht.",
    ])
    rec = parse_recording({"id": "r", "title": "x", "transcript": text})
    assert rec.segments == [] and rec.transcript_text == text


def test_upgrade_rewrites_files_from_stored_json(settings):
    # A file written by 1.0.0, which missed the nested action items and filed it by hand under Acme
    old = dict(CURRENT, transcript="Hoe kijk je terug op de stage? Ik heb veel geleerd over data.",
               summarizations={"sum_456": {"v2": {"summary": {"markdown": "## Kern\nTerugblik op de stage."}}}})
    no_raw = {"id": "rec_gone", "title": "Zonder JSON", "recording_at": "2026-09-20T10:00:00Z",
              "updated_at": "2026-09-20T11:00:00Z", "transcript": "Kort."}
    sync.run_sync(settings, transport=make_transport({"rec_now": old, "rec_gone": no_raw}))
    index = Index(settings)
    sync.move_row(settings, index, index.get("rec_now"), "Acme", None, "manual")
    index.close()
    raw = settings.root / ".pocket-bridge" / "raw"
    (raw / "rec_gone.json").unlink()
    (raw / "rec_now.json").write_text(json.dumps(CURRENT), encoding="utf-8")
    state = sync.load_state(settings)
    state["format"] = 1
    sync.save_state(settings, state)

    plain = next(settings.root.rglob("*Zonder JSON.md"))
    plain.write_text(plain.read_text(encoding="utf-8") + "\nMijn eigen notitie.\n", encoding="utf-8")

    assert sync.upgrade(settings) == 1
    path = next(settings.root.rglob("*Reflective interview.md"))
    backup = settings.root / ".pocket-bridge" / "backup" / "rec_now.md"
    assert "Hoe kijk je terug" in backup.read_text(encoding="utf-8") and "Verslag insturen" not in backup.read_text(encoding="utf-8")
    assert "Mijn eigen notitie." in plain.read_text(encoding="utf-8")  # nothing to gain: left alone
    assert path.parent.parent.name == "Acme"
    parsed = parse_markdown(path)
    assert parsed.action_items == [(False, "Verslag insturen (2026-10-01)"), (True, "Beoordeling invullen")]
    assert speakers_in(path.read_text(encoding="utf-8")) == ["Speaker 1", "Speaker 2"]
    assert "manual" in path.read_text(encoding="utf-8")
    assert "Verslag insturen" in (settings.root / "Klanten" / "Acme" / "_Dossier.md").read_text(encoding="utf-8")

    state = sync.load_state(settings)
    assert state["format"] == sync.FORMAT_VERSION and state["refetch"] is True
    assert "rec_gone" not in state["recordings"]
    assert sync.upgrade(settings) == 0  # only once

    # The next sync fetches everything again (also old recordings) and clears the flag
    transport = make_transport({"rec_now": CURRENT, "rec_gone": no_raw})
    res = sync.run_sync(settings, transport=transport)
    assert not res.errors and (raw / "rec_gone.json").exists()
    assert "Mijn eigen notitie." in (raw.parent / "backup" / "rec_gone.md").read_text(encoding="utf-8")
    assert "refetch" not in sync.load_state(settings)
    assert parse_markdown(path).action_items[1] == (True, "Beoordeling invullen")


def test_fresh_install_starts_on_current_format(settings):
    sync.run_sync(settings, transport=make_transport({"rec_now": CURRENT}))
    assert sync.load_state(settings)["format"] == sync.FORMAT_VERSION
    path = next(Path(settings.root).rglob("*Reflective interview.md"))
    assert parse_markdown(path).action_items[0] == (False, "Verslag insturen (2026-10-01)")


def test_shape_command_hides_content(settings, capsys):
    from pocket_bridge.__main__ import main

    sync.run_sync(settings, transport=make_transport({"rec_now": CURRENT}))
    main(["shape", "rec_now"])
    out = capsys.readouterr().out
    assert "Verslag insturen" not in out and "stage" not in out
    assert '"actionItems"' in out and "<text, " in out
    assert "segments: 2, speakers: 2, action items: 2" in out
