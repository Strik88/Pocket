"""Pocket's API has sent recordings in several shapes. These tests pin the ones seen in the wild,
plus the upgrade that rewrites existing files once the reading improves."""

import json
from pathlib import Path

from pocket_bridge import sync
from pocket_bridge.index import Index
from pocket_bridge.pocket_api import Segment, parse_recording
from pocket_bridge.storage import FORMAT_VERSION, parse_markdown, render_markdown, speakers_in

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


def test_memos_and_colon_sentences_get_no_fake_speakers():
    for text in (
        "Agenda maandag 09:00\nOverleg met Acme over de offerte en de planning.\nAgenda dinsdag 14:30\nWerkcollege voorbereiden.",
        "Boodschappen: melk, brood en kaas.\nTodo: Jan bellen over de offerte.",
        "Kijk: het punt is de planning.\nDat klopt, maar het budget ook.\nMijn voorstel: we schuiven een week.\nPrima, dan doen we dat.",
    ):
        rec = parse_recording({"id": "r", "title": "x", "transcript": text})
        assert rec.segments == [] and rec.transcript_text == text


def test_colon_lines_inside_a_turn_stay_in_the_turn():
    rec = parse_recording({"id": "r", "title": "x", "transcript": (
        "Speaker 1: We lopen de agenda door.\nPunt een: de begroting.\nPunt twee: de planning.\n"
        "Speaker 2: Akkoord.\nSpeaker 1: Dan beginnen we."
    )})
    assert [s.speaker for s in rec.segments] == ["Speaker 1", "Speaker 2", "Speaker 1"]
    assert rec.segments[0].text.endswith("Punt twee: de planning.")


def test_named_dialogue_in_plain_text_only_with_listed_speakers():
    text = "Ian: Hoe gaat het?\nJan: Goed.\nIan: Mooi.\nJan: En met jou?"
    assert parse_recording({"id": "r", "title": "x", "transcript": text}).segments == []
    rec = parse_recording({"id": "r", "title": "x", "transcript": text, "speakers": [{"id": "a", "name": "Ian"}, {"id": "b", "name": "Jan"}]})
    assert [s.speaker for s in rec.segments] == ["Ian", "Jan", "Ian", "Jan"]


def test_repeated_labels_in_notes_stay_text():
    for text in (
        "Agenda maandag 09:00\nOverleg met Acme.\nAgenda maandag 11:30\nLunch met Jan.\nAgenda dinsdag 14:00\nWerkcollege.\nAgenda dinsdag 16:30\nBorrel.",
        "Todo: Jan bellen.\nTodo: offerte sturen.\nBoodschappen: melk.\nBoodschappen: brood.",
        "Besluit: we gaan door.\nActie: Ian plant.\nBesluit: budget blijft.\nActie: Jan belt.",
    ):
        rec = parse_recording({"id": "r", "title": "x", "transcript": text})
        assert rec.segments == [] and rec.transcript_text == text


def test_prose_with_a_colon_is_left_alone():
    text = "\n".join([
        "Vandaag gaan we het hebben over regressie.",
        "Let op: dit komt terug op het tentamen.",
        "We beginnen met een voorbeeld uit de praktijk.",
        "Daarna kijken we naar de opdracht.",
    ])
    rec = parse_recording({"id": "r", "title": "x", "transcript": text})
    assert rec.segments == [] and rec.transcript_text == text


def as_v100(settings, data, labels=None, names=None, keep_actions=False, note=""):
    """Sync `data`, then put the file and state back the way 1.0.0 left them: its speaker labels
    (labels per segment, "" for none), no action items, names the user gave, format 1."""
    sync.run_sync(settings, transport=make_transport({data["id"]: data}))
    rec = parse_recording(data)
    if labels is not None:
        rec.segments = [Segment(text=seg.text, speaker=label, start=seg.start) for seg, label in zip(rec.segments, labels)]
    if not keep_actions:
        rec.action_items, rec.actions_completed = [], set()
    index = Index(settings)
    row = index.get(data["id"])
    index.close()
    path = Path(row.path)
    text = render_markdown(settings, rec, row.client, "rule: x", row.project, None, names, set())
    text = text.replace(f"bridge_format: {FORMAT_VERSION}\n", "")  # 1.0.0 did not stamp its files
    path.write_text(text + note, encoding="utf-8")
    state = sync.load_state(settings)
    state["format"] = 1
    state["speakers"] = {data["id"]: dict(names)} if names else {}
    sync.save_state(settings, state)
    Index(settings).refresh()
    return path


def turns(path):
    """(speaker heading, first line said) in transcript order."""
    lines = path.read_text(encoding="utf-8").split("## Transcript", 1)[1].splitlines()
    return [(ln.strip("*").split("**")[0], lines[i + 1]) for i, ln in enumerate(lines) if ln.startswith("**")]


def test_upgrade_rewrites_files_from_stored_json(settings):
    path = as_v100(settings, CURRENT)
    # 1.0.0 read the plain transcript: no speakers in the file
    (settings.root / ".pocket-bridge" / "raw" / "rec_now.json").write_text(json.dumps(CURRENT), encoding="utf-8")
    path.write_text(path.read_text(encoding="utf-8").replace("**Speaker 1** (00:00:00)\n", "").replace("**Speaker 2** (00:00:03)\n", ""), encoding="utf-8")
    assert speakers_in(path.read_text(encoding="utf-8")) == []
    index = Index(settings)
    path = sync.move_row(settings, index, index.get("rec_now"), "Acme", None, "manual")
    index.close()

    assert sync.upgrade(settings) == 1
    assert path.parent.parent.name == "Acme" and "manual" in path.read_text(encoding="utf-8")
    assert parse_markdown(path).action_items == [(False, "Verslag insturen (2026-10-01)"), (True, "Beoordeling invullen")]
    assert speakers_in(path.read_text(encoding="utf-8")) == ["Speaker 1", "Speaker 2"]
    assert "Verslag insturen" in (settings.root / "Klanten" / "Acme" / "_Dossier.md").read_text(encoding="utf-8")
    backup = settings.root / ".pocket-bridge" / "backup" / "rec_now.md"
    assert "Verslag insturen" not in backup.read_text(encoding="utf-8")
    state = sync.load_state(settings)
    assert state["format"] == sync.FORMAT_VERSION and "refetch_ids" not in state
    assert sync.upgrade(settings) == 0  # only once


def test_upgrade_keeps_speaker_names_with_the_same_person(settings):
    # 1.0.0 showed Pocket's "Speaker 0"/"Speaker 1"; the user named person B (Speaker 1) Jan
    data = dict(CURRENT, transcript=[
        {"speaker": "Speaker 0", "text": "Ik ben persoon A.", "start": 0},
        {"speaker": "Speaker 1", "text": "Ik ben persoon B.", "start": 2},
    ])
    path = as_v100(settings, data, labels=["Speaker 0", "Jan"], names={"Speaker 1": "Jan"})
    sync.upgrade(settings)
    assert turns(path) == [("Speaker 1", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]
    assert sync.load_state(settings)["speakers"]["rec_now"] == {"Speaker 2": "Jan"}


def test_upgrade_keeps_names_given_to_numbered_speakers(settings):
    # Pocket numbered from 0: 1.0.0 dropped speaker 0 and showed speaker 1 as "1", which was named Jan
    data = dict(CURRENT, transcript=[
        {"speaker": 0, "text": "Vraag van A.", "start": 0},
        {"speaker": 1, "text": "Antwoord van B.", "start": 2},
        {"speaker": "SPEAKER_02", "text": "Derde persoon.", "start": 4},
    ])
    path = as_v100(settings, data, labels=["", "Jan", "Petra"], names={"1": "Jan", "SPEAKER_02": "Petra"})
    sync.upgrade(settings)
    assert turns(path) == [("Speaker 1", "Vraag van A."), ("Jan", "Antwoord van B."), ("Petra", "Derde persoon.")]


def test_upgrade_leaves_files_alone_that_gain_nothing(settings):
    # Pocket split Ian over Speaker 1 and 3; the user named both Ian. Nothing to gain: notes stay.
    data = dict(CURRENT, transcript=[
        {"speaker": "Speaker 1", "text": "Een.", "start": 0},
        {"speaker": "Speaker 2", "text": "Twee.", "start": 1},
        {"speaker": "Speaker 3", "text": "Drie.", "start": 2},
    ])
    names = {"Speaker 1": "Ian", "Speaker 2": "Jan", "Speaker 3": "Ian"}
    path = as_v100(settings, data, labels=["Ian", "Jan", "Ian"], names=names, keep_actions=True, note="\nMijn notitie.\n")
    assert sync.upgrade(settings) == 0
    assert "Mijn notitie." in path.read_text(encoding="utf-8")
    assert not (settings.root / ".pocket-bridge" / "backup").exists()


def test_upgrade_keeps_ticks_when_wording_shifts(settings):
    data = dict(CURRENT, summarizations={"s": {"v2": {"actionItems": {"actionItems": [
        {"title": "Offerte sturen", "assignee": {"name": "Ian"}},
        {"title": "Bel  Petra"},
        {"title": "Nieuw punt"},
    ]}}}})
    path = as_v100(settings, data, labels=["", ""])
    text = path.read_text(encoding="utf-8").replace("## Transcript", "## Actiepunten\n\n- [x] Offerte sturen\n- [x] Bel  Petra\n\n## Transcript")
    path.write_text(text, encoding="utf-8")
    Index(settings).refresh()
    sync.upgrade(settings)  # gains speakers
    assert parse_markdown(path).action_items == [(True, "Offerte sturen (Ian)"), (True, "Bel Petra"), (False, "Nieuw punt")]


def test_stale_stored_json_is_not_used(settings):
    # Keeping raw data was switched off later: raw/ still holds an older version than the file
    newer = dict(CURRENT, title="Interview definitief", updated_at="2026-09-30T10:00:00Z")
    path = as_v100(settings, newer)
    (settings.root / ".pocket-bridge" / "raw" / "rec_now.json").write_text(json.dumps(CURRENT), encoding="utf-8")
    assert sync.upgrade(settings) == 0
    assert "Interview definitief" in path.read_text(encoding="utf-8")
    assert sync.load_state(settings)["refetch_ids"] == {"rec_now": 0}

    res = sync.run_sync(settings, transport=make_transport({"rec_now": newer}))
    assert not res.errors and "refetch_ids" not in sync.load_state(settings)
    text = path.read_text(encoding="utf-8")
    assert "Interview definitief" in text and "Verslag insturen" in text


def test_without_stored_json_only_gaining_files_are_fetched_and_rewritten(settings):
    settings.keep_raw_json = False
    plain = {"id": "rec_plain", "title": "Losse notitie", "recording_at": "2026-09-20T10:00:00Z",
             "updated_at": "2026-09-20T11:00:00Z", "transcript": "Kort."}
    gone = {"id": "rec_failing", "title": "Weg bij Pocket", "recording_at": "2026-09-21T10:00:00Z",
            "updated_at": "2026-09-21T11:00:00Z", "transcript": "Ook kort."}
    sync.run_sync(settings, transport=make_transport({"rec_plain": plain, "rec_failing": gone}))
    for f in settings.root.rglob("*.md"):  # as 1.0.0 wrote them
        f.write_text(f.read_text(encoding="utf-8").replace(f"bridge_format: {FORMAT_VERSION}\n", ""), encoding="utf-8")
    path = as_v100(settings, CURRENT, note="\nNotitie bij interview.\n")
    plain_path = next(settings.root.rglob("*Losse notitie.md"))
    plain_path.write_text(plain_path.read_text(encoding="utf-8") + "\nMijn notitie.\n", encoding="utf-8")
    state = sync.load_state(settings)
    state["last_sync"] = "2026-10-06T10:00:00"
    sync.save_state(settings, state)

    sync.upgrade(settings)
    assert sorted(sync.load_state(settings)["refetch_ids"]) == ["rec_failing", "rec_now", "rec_plain"]
    for attempt in range(4):  # Pocket no longer has rec_failing (404)
        transport = make_transport({"rec_now": CURRENT, "rec_plain": plain})
        res = sync.run_sync(settings, transport=transport)
        assert not res.errors
        listing = [c for c in transport.calls if c.url.path.endswith("/public/recordings")][0]
        assert listing.url.params.get("startDate") == "2026-10-03" or attempt  # no full sync
    assert not any(c.url.path.endswith("/rec_failing") for c in transport.calls)  # given up after 3 tries
    assert sync.load_state(settings)["refetch_ids"] == {"rec_failing": 3}
    assert "Mijn notitie." in plain_path.read_text(encoding="utf-8")  # nothing gained: untouched
    assert "Verslag insturen" in path.read_text(encoding="utf-8")
    assert "Notitie bij interview." in (settings.root / ".pocket-bridge" / "backup" / "rec_now.md").read_text(encoding="utf-8")


def test_backup_is_never_overwritten(settings, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("met notitie", encoding="utf-8")
    sync._backup(settings, "rec_x", f)
    f.write_text("herschreven", encoding="utf-8")
    sync._backup(settings, "rec_x", f)
    assert (settings.root / ".pocket-bridge" / "backup" / "rec_x.md").read_text(encoding="utf-8") == "met notitie"


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


def test_shape_asks_for_the_id_when_titles_repeat(settings, capsys):
    import pytest

    from pocket_bridge.__main__ import main

    second = dict(CURRENT, id="rec_two", recording_at="2026-09-26T13:35:00Z")
    sync.run_sync(settings, transport=make_transport({"rec_now": CURRENT, "rec_two": second}))
    with pytest.raises(SystemExit):
        main(["shape", "Reflective interview"])
    out = capsys.readouterr().out
    assert "rec_now" in out and "rec_two" in out
    main(["shape", "rec_two"])
    assert "recording: rec_two" in capsys.readouterr().out


def test_names_given_after_an_upgrade_that_left_the_file_alone(settings):
    # 1.0.0 already showed the action items and both speakers ("Speaker 0", "Speaker 1"): nothing to gain
    data = dict(CURRENT, transcript=[
        {"speaker": "Speaker 0", "text": "Ik ben persoon A.", "start": 0},
        {"speaker": "Speaker 1", "text": "Ik ben persoon B.", "start": 2},
    ])
    path = as_v100(settings, data, labels=["Speaker 0", "Speaker 1"], keep_actions=True, note="\nNotitie.\n")
    assert sync.upgrade(settings) == 0
    assert turns(path) == [("Speaker 1", "Ik ben persoon A."), ("Speaker 2", "Ik ben persoon B.")]
    assert "Notitie." in path.read_text(encoding="utf-8")
    sync.rename_speakers(settings, "rec_now", {"Speaker 2": "Jan"})
    newer = dict(data, updated_at="2026-10-01T09:00:00Z")
    sync.run_sync(settings, transport=make_transport({"rec_now": newer}), full=True)
    assert turns(path) == [("Speaker 1", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]


def test_names_migrate_when_pocket_updates_a_recording_given_up_on(settings):
    settings.keep_raw_json = False
    data = dict(CURRENT, transcript=[
        {"speaker": "Speaker 0", "text": "Ik ben persoon A.", "start": 0},
        {"speaker": "Speaker 1", "text": "Ik ben persoon B.", "start": 2},
    ])
    path = as_v100(settings, data, labels=["Speaker 0", "Jan"], names={"Speaker 1": "Jan"})
    sync.upgrade(settings)
    for _ in range(3):
        sync.run_sync(settings, transport=make_transport({}))  # Pocket keeps failing (404)
    newer = dict(data, updated_at="2026-10-01T09:00:00Z")
    sync.run_sync(settings, transport=make_transport({"rec_now": newer}), full=True)
    assert turns(path) == [("Speaker 1", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]
    assert "refetch_ids" not in sync.load_state(settings)


def test_stored_json_without_updated_at_is_not_trusted(settings):
    undated = dict(CURRENT, updated_at="")
    as_v100(settings, undated)
    sync.upgrade(settings)
    assert sync.load_state(settings)["refetch_ids"] == {"rec_now": 0}


def test_ticks_never_jump_to_an_item_left_open():
    from pocket_bridge.storage import still_ticked

    assert still_ticked(["Bel Petra (Jan)"], [(True, "Bel Petra (Ian)"), (False, "Bel Petra (Jan)")]) == set()
    assert still_ticked(["Factuur sturen (project B)"], [(True, "Factuur sturen (project A)")]) == set()
    assert still_ticked(["Offerte sturen (Ian)", "Bel Petra", "Verslag (2026-10-01)"],
                        [(True, "Offerte sturen"), (True, "Bel  Petra"), (True, "Verslag (2026-10-01T00:00:00Z)")]) == {
        "Offerte sturen (Ian)", "Bel Petra", "Verslag (2026-10-01)"}


SPK0 = dict(CURRENT, transcript=[
    {"speaker": "Speaker 0", "text": "Ik ben persoon A.", "start": 0},
    {"speaker": "Speaker 1", "text": "Ik ben persoon B.", "start": 2},
])


def test_upgrade_that_stopped_halfway_can_run_again(settings, monkeypatch):
    import pytest

    path = as_v100(settings, SPK0, labels=["Speaker 0", "Jan"], names={"Speaker 1": "Jan"}, keep_actions=True)
    real_save = sync.save_state

    def crash(*a, **k):
        raise KeyboardInterrupt("app closed")

    monkeypatch.setattr(sync, "save_state", crash)
    with pytest.raises(KeyboardInterrupt):
        sync.upgrade(settings)  # the file is aligned, the state is not saved
    monkeypatch.setattr(sync, "save_state", real_save)
    sync.upgrade(settings)
    sync.upgrade(settings)
    assert turns(path) == [("Speaker 1", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]
    sync.rename_speakers(settings, "rec_now", {"Speaker 1": "Ian"})
    sync.run_sync(settings, transport=make_transport({"rec_now": dict(SPK0, updated_at="2026-10-01T09:00:00Z")}), full=True)
    assert turns(path) == [("Ian", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]


def test_a_locked_file_does_not_stop_the_sync_or_move_names_twice(settings, monkeypatch):
    path = as_v100(settings, SPK0, labels=["Ian", "Speaker 1"], names={"Speaker 0": "Ian"}, keep_actions=True)
    real_write = sync.write_text_atomic

    def locked(*a, **k):
        raise PermissionError("in use by OneDrive")

    monkeypatch.setattr(sync, "write_text_atomic", locked)
    res = sync.run_sync(settings, transport=make_transport({"rec_now": SPK0}))
    assert not res.message.startswith("in use")
    state = sync.load_state(settings)
    assert state["speakers"]["rec_now"] == {"Speaker 0": "Ian"} and state["refetch_ids"] == {"rec_now": 1}
    monkeypatch.setattr(sync, "write_text_atomic", real_write)
    sync.run_sync(settings, transport=make_transport({"rec_now": SPK0}))
    assert turns(path) == [("Ian", "Ik ben persoon A."), ("Speaker 2", "Ik ben persoon B.")]
    sync.run_sync(settings, transport=make_transport({"rec_now": dict(SPK0, updated_at="2026-10-01T09:00:00Z")}), full=True)
    assert turns(path) == [("Ian", "Ik ben persoon A."), ("Speaker 2", "Ik ben persoon B.")]
    assert "refetch_ids" not in sync.load_state(settings)


def test_name_next_to_pocket_labels_in_plain_text():
    text = ("Ian Striks: Goedemorgen, zullen we beginnen?\nUnknown Speaker 2: Ja, prima.\nIan Striks: Eerst de planning.\n"
            "Unknown Speaker 2: Die loopt uit.\nUnknown Speaker 3: Dat lijkt me haalbaar.")
    # Pocket did not list Ian Striks: a name that comes back could be a speaker or part of the text; keep the text
    rec = parse_recording({"id": "r", "title": "x", "transcription": {"transcription": {"text": text}}})
    assert rec.segments == [] and rec.transcript_text == text
    rec = parse_recording({"id": "r", "title": "x", "speakers": [{"id": "1", "name": "Ian Striks"}],
                           "transcription": {"transcription": {"text": text}}})
    assert [s.speaker for s in rec.segments] == ["Ian Striks", "Unknown Speaker 2", "Ian Striks", "Unknown Speaker 2", "Unknown Speaker 3"]


def test_repeated_labels_inside_a_transcript_keep_it_plain():
    for text in (
        "Speaker 1: We lopen de acties door.\nActie: Ian stuurt de offerte.\nActie: Jan plant de demo.\nSpeaker 2: Akkoord.\nSpeaker 1: Dan beginnen we.",
        "Speaker 1: Notities van vandaag.\nTodo: Jan bellen.\nTodo: offerte sturen.\nBesluit: we gaan door.",
        "Notulen.\nSpreker 1: Jan de Vries, inkoop.\nActie: Jan stuurt de offerte.\nActie: Ian plant de kickoff.",
    ):
        rec = parse_recording({"id": "r", "title": "x", "transcript": text})
        assert rec.segments == [] and rec.transcript_text == text


def test_notes_in_a_file_do_not_block_the_relabel(settings):
    # A bold line in notes the user added, and a heading typed by hand, are left alone
    data = dict(CURRENT, transcript=[
        {"speaker": "Speaker 0", "text": "Ik ben persoon A.", "start": 0},
        {"speaker": "Speaker 1", "text": "Ik ben persoon B.", "start": 2},
        {"speaker": "Speaker 2", "text": "Ik ben persoon C.", "start": 4},
    ])
    path = as_v100(settings, data, labels=["Ian", "Speaker 1", "Speaker 2"], keep_actions=True,
                   note="\n## Notities\n\n**Vervolgafspraak**\nDinsdag bellen.\n")
    sync.upgrade(settings)
    assert turns(path)[:3] == [("Ian", "Ik ben persoon A."), ("Speaker 2", "Ik ben persoon B."), ("Speaker 3", "Ik ben persoon C.")]
    assert "**Vervolgafspraak**" in path.read_text(encoding="utf-8")
    sync.rename_speakers(settings, "rec_now", {"Speaker 2": "Jan"})
    sync.run_sync(settings, transport=make_transport({"rec_now": dict(data, updated_at="2026-10-01T09:00:00Z")}), full=True)
    assert turns(path)[1] == ("Jan", "Ik ben persoon B.")


def test_a_deleted_file_that_comes_back_gets_its_names_moved(settings):
    settings.keep_raw_json = False
    path = as_v100(settings, SPK0, labels=["Ian", "Jan"], names={"Speaker 0": "Ian", "Speaker 1": "Jan"})
    sync.upgrade(settings)
    path.unlink()
    sync.run_sync(settings, transport=make_transport({}))  # the refetch finds no file: the id waits
    assert "rec_now" in sync.load_state(settings)["refetch_ids"]
    sync.run_sync(settings, transport=make_transport({"rec_now": SPK0}), full=True)  # brings the file back
    back = next(settings.root.rglob("*Reflective interview.md"))
    assert turns(back) == [("Ian", "Ik ben persoon A."), ("Jan", "Ik ben persoon B.")]
    assert "refetch_ids" not in sync.load_state(settings)
