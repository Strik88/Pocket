import json

from fastapi.testclient import TestClient

from pocket_bridge import claude_connect, sync
from pocket_bridge.config import load_settings
from pocket_bridge.web import app as webapp

from .conftest import make_transport


def client():
    from pocket_bridge.web import session

    return TestClient(webapp.app, base_url="http://127.0.0.1:8765", headers=session.headers())


def test_state_hides_keys(settings):
    r = client().get("/api/state").json()
    assert r["pocket_ready"] is True
    assert "pocket_api_key" not in r["settings"]
    assert r["settings"]["pocket_api_key_masked"] == "•••"  # short keys are fully hidden


def test_foreign_host_blocked(settings):
    c = TestClient(webapp.app, base_url="http://evil.example.com")
    assert c.get("/api/state").status_code == 403


def test_clients_crud(settings):
    c = client()
    c.post("/api/clients", json={"name": "Gamma", "keywords": ["gamma", " "], "pocket_tags": [], "notes": ""})
    names = [x["name"] for x in c.get("/api/clients").json()["clients"]]
    assert "Gamma" in names
    c.post("/api/clients?original_name=Gamma", json={"name": "Gamma BV", "keywords": ["g"], "pocket_tags": [], "notes": ""})
    assert load_settings().find_client("Gamma BV").keywords == ["g"]
    c.delete("/api/clients/Gamma BV")
    assert not load_settings().find_client("Gamma BV")


def test_recordings_and_assign(settings):
    sync.run_sync(settings, transport=make_transport())
    c = client()
    recs = c.get("/api/recordings").json()["recordings"]
    assert len(recs) == 3
    assert c.get("/api/recordings?q=facturatie").json()["recordings"][0]["pocket_id"] == "rec_beta"
    assert len(c.get("/api/recordings?unsorted=true").json()["recordings"]) == 1
    detail = c.get("/api/recordings/rec_acme").json()
    assert "Offerte sturen" in detail["markdown"]
    c.post("/api/recordings/rec_misc/assign", json={"client": "Acme"})
    assert len(c.get("/api/recordings?client=Acme").json()["recordings"]) == 2


def test_settings_language_sets_folder_names(settings):
    c = client()
    c.post("/api/settings", json={"language": "en"})
    s = load_settings()
    assert s.clients_dirname == "Clients" and s.unsorted_dirname == "_Unsorted"


def test_connect_claude_desktop_merges_and_backs_up(settings, tmp_path, monkeypatch):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    monkeypatch.setattr(claude_connect, "claude_desktop_config_path", lambda: cfg)
    r = client().post("/api/connect-claude-desktop").json()
    assert r["ok"]
    data = json.loads(cfg.read_text())
    assert set(data["mcpServers"]) == {"other", "pocket-transcripts"}
    assert data["theme"] == "dark"
    assert data["mcpServers"]["pocket-transcripts"]["args"] == ["-m", "pocket_bridge", "mcp"]
    assert list(cfg.parent.glob("claude_desktop_config.backup-*.json"))


def test_index_page_served(settings):
    r = client().get("/")
    assert r.status_code == 200 and "Pocket Bridge" in r.text
    assert client().get("/static/js/main.js").status_code == 200


def test_actions_endpoints(settings):
    sync.run_sync(settings, transport=make_transport())
    c = client()
    items = c.get("/api/actions").json()["actions"]
    assert len(items) == 2
    c.post("/api/actions", json={"pocket_id": "rec_acme", "text": "Offerte sturen (Ian)", "done": True})
    assert len(c.get("/api/actions").json()["actions"]) == 1
    assert len(c.get("/api/actions?include_done=true").json()["actions"]) == 2


def test_recording_detail_has_speakers_and_assign_suggests(settings):
    sync.run_sync(settings, transport=make_transport())
    c = client()
    d = c.get("/api/recordings/rec_beta").json()
    assert d["speakers"] == ["A", "B"] and d["actions"][0]["text"] == "Demo plannen"
    assert c.post("/api/recordings/rec_beta/speakers", json={"mapping": {"A": "Anna"}}).json()["changed"] == 1
    r = c.post("/api/recordings/rec_misc/assign", json={"client": "Acme", "project": "Intern"}).json()
    assert "Intern" in r["path"] and isinstance(r["suggestions"], list)


def test_weekly_endpoints(settings):
    sync.run_sync(settings, transport=make_transport())
    c = client()
    r = c.post("/api/weekly", json={"week": "2026-W36", "ai": False}).json()
    assert "Kickoff Acme" in r["markdown"]
    assert c.get("/api/weekly?week=2026-W36").json()["markdown"] == r["markdown"]


def test_calendar_urls_not_in_state(settings):
    c = client()
    c.post("/api/settings", json={"calendar_urls": ["https://cal.example.com/secret.ics", " "]})
    st = c.get("/api/state").json()
    assert "calendar_urls" not in st["settings"] and st["settings"]["calendar_count"] == 1
    assert c.get("/api/calendar-urls").json()["urls"] == ["https://cal.example.com/secret.ics"]


def test_ask_streams_sources_text_and_citations(settings, monkeypatch):
    from pocket_bridge import ai

    sync.run_sync(settings, transport=make_transport())
    s = load_settings()
    s.anthropic_api_key = "sk-test"
    from pocket_bridge.config import save_settings

    save_settings(s)

    def fake_stream(settings, question, sources, history=None):
        assert sources and sources[0]["pocket_id"] == "rec_beta"
        yield {"type": "text", "text": "Facturatie loopt achter."}
        yield {"type": "done", "blocks": [{"text": "Facturatie loopt achter.", "citations": [{"source": 0, "cited_text": "facturatie loopt achter"}]}]}

    monkeypatch.setattr(ai, "ask_stream", fake_stream)
    with client().stream("POST", "/api/ask", json={"question": "Hoe staat het met de facturatie?"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["sources", "text", "done"]
    assert events[0]["sources"][0]["title"] == "Gesprek over facturen"
    assert events[2]["blocks"][0]["citations"][0]["source"] == 0


def test_ping(settings):
    assert client().get("/api/ping").json()["app"] == "pocket-bridge"


def test_connect_writes_store_config_on_windows(tmp_path, monkeypatch):
    """Claude Desktop from the Microsoft Store reads a virtualised config path; write both."""
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    store = local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    store.mkdir(parents=True)
    (store / "claude_desktop_config.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    monkeypatch.setattr(claude_connect.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    claude_connect.connect_claude_desktop()
    for cfg in (roaming / "Claude" / "claude_desktop_config.json", store / "claude_desktop_config.json"):
        assert "pocket-transcripts" in json.loads(cfg.read_text())["mcpServers"]
    assert "other" in json.loads((store / "claude_desktop_config.json").read_text())["mcpServers"]
    assert claude_connect.is_connected()


def test_mcp_uses_python_not_pythonw(tmp_path, monkeypatch):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "python.exe").write_text("")
    (scripts / "pythonw.exe").write_text("")
    monkeypatch.setattr(claude_connect.sys, "executable", str(scripts / "pythonw.exe"))
    assert claude_connect.server_entry()["command"].endswith("python.exe")
    assert "pythonw" not in claude_connect.claude_code_command()
