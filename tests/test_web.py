import json

from fastapi.testclient import TestClient

from pocket_bridge import claude_connect, sync
from pocket_bridge.config import load_settings
from pocket_bridge.web import app as webapp

from .conftest import make_transport


def client():
    return TestClient(webapp.app, base_url="http://127.0.0.1:8765")


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
    assert client().get("/static/app.js").status_code == 200
