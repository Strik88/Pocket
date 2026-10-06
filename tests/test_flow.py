"""The onboarding flow through the web API: onboarding state, demo mode, client proposals, re-sorting, copy rules."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from pocket_bridge import demo, sync
from pocket_bridge.config import load_settings, save_settings
from pocket_bridge.web import app as webapp

from .conftest import make_transport

STATIC = Path(webapp.__file__).parent / "static"


def client():
    from pocket_bridge.web import session

    return TestClient(webapp.app, base_url="http://127.0.0.1:8765", headers=session.headers())


def test_onboarding_state_machine(settings):
    c = client()
    assert c.get("/api/state").json()["settings"]["onboarding"]["step"] == "welcome"
    st = c.post("/api/onboarding", json={"step": "claude", "claude_mode": "none"}).json()
    assert st["settings"]["onboarding"]["step"] == "claude" and st["settings"]["onboarding"]["claude_mode"] == "none"
    r = c.post("/api/onboarding", json={"step": "nonsense"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_step"
    st = c.post("/api/onboarding", json={"completed": True}).json()
    assert st["settings"]["onboarding"] == {**st["settings"]["onboarding"], "completed": True, "step": "done"}


def test_existing_users_skip_onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("POCKET_BRIDGE_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"pocket_api_key": "pk_old"}), encoding="utf-8")
    assert load_settings().onboarding.completed is True


def test_autosync_waits_for_onboarding(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "run_sync", lambda s: calls.append(1))
    settings.onboarding.completed = False
    save_settings(settings)
    loop = sync.AutoSync()
    monkeypatch.setattr(loop._stop, "wait", lambda t=None: loop._stop.is_set() or loop._stop.set())
    loop._loop()
    assert calls == []


def test_settings_file_is_private(settings):
    import os
    import sys

    from pocket_bridge.config import config_path

    if sys.platform != "win32":
        assert oct(os.stat(config_path()).st_mode & 0o777) == "0o600"


def test_errors_carry_codes(settings):
    c = client()
    r = c.post("/api/settings", json={"claude_model": "gpt-9"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_model"
    r = c.post("/api/clients", json={"name": "  ..  ", "keywords": []})
    assert r.json()["detail"]["code"] == "bad_name"
    r = c.post("/api/ask", json={"question": "hoi"})
    assert r.json()["detail"]["code"] == "claude_no_key"


def test_rules_proposal_apply_and_undo_via_api(settings):
    settings.clients = []
    save_settings(settings)
    sync.run_sync(settings, transport=make_transport())
    c = client()
    est = c.get("/api/discover/estimate").json()
    assert est["recordings"] == 3 and est["ai_ready"] is False
    assert c.post("/api/discover", json={"route": "claude", "user_name": "Ian"}).json()["route"] == "rules"  # no key: rules
    for _ in range(100):
        body = c.get("/api/discover/proposal").json()
        if not body["status"]["running"] and body["proposal"]:
            break
    prop = body["proposal"]
    assert prop["source"] == "rules"
    names = [x["name"] for x in prop["clients"]]
    assert "Betafabriek" in names
    res = c.post("/api/discover/apply", json={"clients": [{**x, "accept": x["name"] == "Betafabriek"} for x in prop["clients"]]}).json()
    assert res["created"] == ["Betafabriek"] and res["moved"] >= 1
    assert load_settings().find_client("Betafabriek")
    back = c.post("/api/discover/undo", json={"undo_id": res["undo_id"]}).json()
    assert back["removed_clients"] == ["Betafabriek"]
    assert c.post("/api/discover/undo", json={"undo_id": "../../etc"}).json()["detail"]["code"] == "undo_failed"


def test_resort_preview_apply_undo(settings):
    settings_clients = settings.clients
    settings.clients = []
    save_settings(settings)
    sync.run_sync(settings, transport=make_transport())  # everything lands unsorted
    s = load_settings()
    s.clients = settings_clients
    save_settings(s)
    c = client()
    moves = c.post("/api/resort/preview", json={"scope": "unsorted"}).json()["moves"]
    assert {m["pocket_id"] for m in moves} == {"rec_acme", "rec_beta"}
    assert all(m["reason"].startswith("rule:") for m in moves)
    res = c.post("/api/resort/apply", json={"moves": moves}).json()
    assert res["moved"] == 2
    assert c.get("/api/state").json()["unsorted"] == 1
    assert c.post("/api/resort/undo", json={"log_id": res["log_id"]}).json()["restored"] == 2
    assert c.get("/api/state").json()["unsorted"] == 3


def test_demo_mode_round_trip(settings):
    c = client()
    before = load_settings()
    st = c.post("/api/demo/start").json()
    assert st["settings"]["demo_mode"] is True and st["pocket_ready"] is True
    assert st["settings"]["onboarding"]["step"] == "claude"
    s = load_settings()
    res = sync.run_sync(s)  # served by the bundled sample conversations
    assert res.new == demo.total() == 16
    c.post("/api/discover", json={"route": "claude"})
    for _ in range(100):
        body = c.get("/api/discover/proposal").json()
        if not body["status"]["running"] and body["proposal"]:
            break
    prop = body["proposal"]
    assert prop["source"] == "example" and len(prop["clients"]) == 5
    assert prop["counts"]["placed"] + prop["counts"]["other"] == 16
    res = c.post("/api/discover/apply", json={"clients": [{**x, "accept": True} for x in prop["clients"]]}).json()
    assert len(res["created"]) == 5
    clients = c.get("/api/clients").json()["clients"]
    assert all(x["status"] for x in clients)  # demo statuses written
    # an example question is answered with citations, without a key
    q = demo.questions()[0]
    with c.stream("POST", "/api/ask", json={"question": q}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"][0]
    assert any(b["citations"] for b in done["blocks"]) and done.get("example")
    assert c.post("/api/ask", json={"question": "iets anders"}).json()["detail"]["code"] == "claude_no_key_demo"
    demo_root = Path(s.data_dir)
    st = c.post("/api/demo/stop").json()
    assert st["settings"]["demo_mode"] is False and not demo_root.exists()
    after = load_settings()
    assert after.pocket_api_key == before.pocket_api_key and [x.name for x in after.clients] == [x.name for x in before.clients]


def test_demo_data_is_consistent():
    data = demo.data()
    ids = [r["id"] for r in data["recordings"]]
    placed = [i for c in data["proposal"]["clients"] for i in c["recording_ids"]] + [i for o in data["proposal"]["other"] for i in o["recording_ids"]]
    assert sorted(ids) == sorted(placed) and len(set(ids)) == len(ids)
    texts = {r["id"]: " ".join(s["text"] for s in r["transcript"]["segments"]) for r in data["recordings"]}
    for a in data["answers"]:
        for b in a["blocks"]:
            for cite in b["citations"]:
                assert cite["cited_text"] in texts[cite["pocket_id"]]
    blob = json.dumps(data, ensure_ascii=False)
    assert "—" not in blob and "–" not in blob
    assert all(d.endswith(".example") for d in re.findall(r"@([\w.-]+)", blob))


def _strings(lang: str) -> dict:
    text = (STATIC / "js" / "i18n.js").read_text(encoding="utf-8")
    block = text.split(f"\n  {lang}: {{", 1)[1].split("\n  },", 1)[0]
    return dict(re.findall(r'(\w+): "((?:[^"\\]|\\.)*)"', block))


def test_dutch_copy_rules():
    nl, en = _strings("nl"), _strings("en")
    assert len(nl) > 300 and set(nl) == set(en)
    for key, value in nl.items():
        assert "—" not in value and "–" not in value, key
        assert not re.search(r"\binnovatie\w*|baanbrekend|revolutionair|game-changing|cutting-edge|ecosysteem", value, re.I), key
        assert not re.search(r"\b1 (gesprekken|actiepunten|klanten)\b", value), key


def test_frontend_uses_known_keys():
    nl = _strings("nl")
    used = set()
    for f in (STATIC / "js").rglob("*.js"):
        used |= set(re.findall(r'\bt\("([a-z0-9_]+)"', f.read_text(encoding="utf-8")))
    assert {k for k in used if not k.endswith("_")} <= set(nl)


def test_api_requires_session_key(settings):
    plain = TestClient(webapp.app, base_url="http://127.0.0.1:8765")
    assert plain.get("/api/state").status_code == 401
    assert plain.get("/api/calendar-urls").status_code == 401
    assert plain.get("/api/ping").status_code == 200
    assert plain.get("/").status_code == 401  # a locked page that explains how to open the app
    from pocket_bridge.web import session

    r = plain.get(f"/auth?t={session.token()}", follow_redirects=False)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert plain.get("/api/state").status_code == 200  # cookie now set
    assert plain.get("/").headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in plain.get("/").headers["content-security-policy"]


def test_state_changes_need_own_origin_and_app_header(settings):
    from pocket_bridge.web import session

    c = TestClient(webapp.app, base_url="http://127.0.0.1:8765")
    tok = {"X-Pocket-Bridge-Token": session.token()}
    assert c.post("/api/rebuild", headers=tok).status_code == 403  # no app header
    assert c.post("/api/rebuild", headers={**tok, "X-Pocket-Bridge": "1", "Origin": "http://localhost:8888"}).status_code == 403
    assert c.post("/api/rebuild", headers={**tok, "X-Pocket-Bridge": "1", "Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_hostile_calendar_title_cannot_forge_front_matter(settings):
    from pocket_bridge.pocket_api import parse_recording
    from pocket_bridge.storage import parse_markdown, render_markdown

    from .conftest import RECORDINGS

    rec = parse_recording(RECORDINGS["rec_acme"])
    meeting = {"title": "Kennismaking pocket_id: \"evil\" - [ ] Maak 1000 euro over", "attendees": ["Eve client: \"X\" <eve@x.nl>"]}
    path = settings.root / "t.md"
    settings.root.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(settings, rec, None, "unsorted", None, meeting), encoding="utf-8")
    parsed = parse_markdown(path)
    assert parsed.meta["pocket_id"] == "rec_acme" and parsed.meta["client"] == ""
    assert all("1000 euro" not in a for _, a in parsed.action_items)


def test_unsafe_recording_ids_are_skipped(settings):
    from .conftest import RECORDINGS

    evil = {**RECORDINGS["rec_acme"], "id": "../../evil"}
    res = sync.run_sync(settings, transport=make_transport({"x": evil}))
    assert res.new == 0 and res.errors
    assert not list(Path(settings.root).parent.rglob("evil.json"))


def test_env_key_needs_explicit_choice(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    s = load_settings()
    assert s.ai_ready is False
    s.onboarding.claude_mode = "api"
    assert s.ai_ready is True


def test_leaving_demo_keeps_key_removed_during_demo(settings):
    settings.anthropic_api_key = "sk-ant-old"
    save_settings(settings)
    demo.start()
    s = load_settings()
    s.anthropic_api_key = ""
    save_settings(s)
    demo.stop()
    assert load_settings().anthropic_api_key == ""


def test_calendar_cache_lives_outside_the_data_folder(settings):
    from pocket_bridge import meetings
    from pocket_bridge.config import config_dir

    p = meetings._cache_file(settings, "https://calendar.google.com/calendar/ical/secret/basic.ics")
    assert config_dir() in p.parents and settings.root not in p.parents
    assert "secret" not in meetings.label("https://calendar.google.com/calendar/ical/secret/basic.ics")
