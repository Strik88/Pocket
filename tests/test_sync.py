import shutil
from pathlib import Path

from pocket_bridge import sync
from pocket_bridge.config import load_settings
from pocket_bridge.index import Index, to_fts_query
from pocket_bridge.storage import parse_markdown, safe_name

from .conftest import RECORDINGS, make_transport


def _files(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))


def test_full_sync_sorts_per_client(settings):
    res = sync.run_sync(settings, transport=make_transport())
    assert res.errors == []
    assert res.new == 3 and res.pending == 1
    root = settings.root
    files = _files(root)
    assert any(f.startswith("Klanten/Acme/2026/") and "Kickoff Acme" in f for f in files)  # name in title
    assert any(f.startswith("Klanten/Betafabriek/2026/") for f in files)  # keyword 2x in transcript
    assert any(f.startswith("_Ongesorteerd/2026/") for f in files)
    assert (root / "Klanten/Acme/_Dossier.md").exists()
    dossier = (root / "Klanten/Acme/_Dossier.md").read_text()
    assert "Offerte sturen" in dossier and "Kickoff Acme" in dossier
    assert (root / ".pocket-bridge/raw/rec_acme.json").exists()


def test_markdown_roundtrip(settings):
    sync.run_sync(settings, transport=make_transport())
    path = next(settings.root.glob("Klanten/Acme/2026/*.md"))
    parsed = parse_markdown(path)
    assert parsed.meta["pocket_id"] == "rec_acme"
    assert parsed.meta["client"] == "Acme"
    assert parsed.action_items == [(False, "Offerte sturen (Ian)")]
    assert "**Jan**" in parsed.transcript


def test_second_sync_skips_unchanged_and_picks_up_finished(settings):
    transport = make_transport()
    sync.run_sync(settings, transport=transport)
    recs = dict(RECORDINGS)
    recs["rec_pending"] = {**RECORDINGS["rec_pending"], "transcript": "Nu wel klaar, Jan van Acme belde.", "updated_at": "2026-09-04T09:00:00Z"}
    res = sync.run_sync(load_settings(), full=True, transport=make_transport(recs))
    assert res.skipped == 3 and res.new == 1 and res.pending == 0


def test_user_move_is_respected(settings):
    sync.run_sync(settings, transport=make_transport())
    src = next(settings.root.glob("_Ongesorteerd/2026/*.md"))
    dest = settings.root / "Klanten" / "Prive" / "2026" / src.name
    dest.parent.mkdir(parents=True)
    shutil.move(src, dest)
    # Pocket reports an update: the file must be rewritten in the new place
    recs = dict(RECORDINGS)
    recs["rec_misc"] = {**RECORDINGS["rec_misc"], "updated_at": "2026-09-05T00:00:00Z", "title": "Losse gedachten v2"}
    sync.run_sync(load_settings(), full=True, transport=make_transport(recs))
    assert dest.exists() and "Losse gedachten v2" in dest.read_text()
    assert not list(settings.root.glob("_Ongesorteerd/**/*.md"))
    idx = Index(load_settings())
    assert idx.get("rec_misc").client == "Prive"
    idx.close()


def test_assign_moves_file_and_updates_dossiers(settings):
    sync.run_sync(settings, transport=make_transport())
    new_path = sync.assign(load_settings(), "rec_misc", "Nieuwe Klant")
    assert "Klanten/Nieuwe Klant/2026" in new_path.as_posix()
    assert parse_markdown(new_path).meta["client"] == "Nieuwe Klant"
    assert load_settings().find_client("nieuwe klant")
    assert (settings.root / "Klanten/Nieuwe Klant/_Dossier.md").exists()
    back = sync.assign(load_settings(), "Losse gedachten", None)
    assert "_Ongesorteerd" in back.as_posix()


def test_dossier_keeps_user_notes(settings):
    sync.run_sync(settings, transport=make_transport())
    d = settings.root / "Klanten/Acme/_Dossier.md"
    d.write_text(d.read_text() + "Belangrijk: beslisser is Jan.\n")
    sync.rebuild(load_settings())
    assert "Belangrijk: beslisser is Jan." in d.read_text()


def test_search(settings):
    sync.run_sync(settings, transport=make_transport())
    idx = Index(load_settings())
    hits = idx.search("facturatie")
    assert hits and hits[0]["pocket_id"] == "rec_beta"
    assert idx.search('"planning voor Q4"')[0]["pocket_id"] == "rec_acme"
    assert idx.search("planning", client="Betafabriek") == []
    assert idx.search("wat is er met de facturatie?")  # stopwords stripped
    assert idx.search('weird"(*) query') == [] or True  # never raises
    idx.close()


def test_no_key_does_nothing(settings):
    settings.pocket_api_key = ""
    assert "API-key" in sync.run_sync(settings).message


def test_helpers():
    assert safe_name('a/b:c*?"<>|. ') == "a-b-c------"
    assert safe_name("CON") == "_CON"
    assert to_fts_query("de planning van Acme") == '"planning"* "acme"*'
