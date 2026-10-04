import json

import httpx
import pytest

from pocket_bridge.config import Client, Settings, save_settings

RECORDINGS = {
    "rec_acme": {
        "id": "rec_acme",
        "title": "Kickoff Acme",
        "recording_at": "2026-09-01T09:30:00Z",
        "updated_at": "2026-09-01T10:30:00Z",
        "duration": 1800,
        "language": "nl",
        "state": "completed",
        "recorded_by": {"display_name": "Ian", "email": "ian@example.com", "user_id": "u1"},
        "tags": [{"id": "t1", "name": "werk", "color": "#f00"}],
        "transcript": [
            {"speaker": "Ian", "text": "Welkom allemaal bij de kickoff.", "start": 0, "end": 3},
            {"speaker": "Jan", "text": "We willen de planning voor Q4 bespreken.", "start": 3, "end": 7},
        ],
        "summarizations": {
            "sum1": {
                "v2": {
                    "summary": {"markdown": "## Kickoff\nPlanning Q4 besproken met Jan."},
                    "actionItems": {"actions": [{"title": "Offerte sturen", "assignee": "Ian"}]},
                }
            }
        },
    },
    "rec_beta": {
        "id": "rec_beta",
        "title": "Gesprek over facturen",
        "recording_at": "2026-09-02T14:00:00Z",
        "updated_at": "2026-09-02T15:00:00Z",
        "duration": 600,
        "tags": [],
        "transcript": {"segments": [
            {"speaker": "A", "text": "Bij Betafabriek loopt de facturatie achter.", "start": 1},
            {"speaker": "B", "text": "Klopt, Betafabriek wil een nieuw systeem.", "start": 5},
        ]},
        "summarizations": [{"summary": "Facturatieproblemen bij Betafabriek.", "action_items": ["Demo plannen"]}],
    },
    "rec_misc": {
        "id": "rec_misc",
        "title": "Losse gedachten",
        "recording_at": "2026-09-03T08:00:00Z",
        "updated_at": "2026-09-03T08:10:00Z",
        "transcript": "Ik moet nog boodschappen doen.",
        "summarizations": None,
    },
    "rec_pending": {
        "id": "rec_pending",
        "title": "Wordt nog verwerkt",
        "recording_at": "2026-09-04T08:00:00Z",
        "updated_at": "2026-09-04T08:00:00Z",
        "state": "processing",
        "transcript": None,
    },
}


def make_transport(recordings=None, key="pk_test", page_size_cap=2):
    recordings = recordings if recordings is not None else RECORDINGS
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("authorization") != f"Bearer {key}":
            return httpx.Response(401, json={"error": "unauthorized"})
        path = request.url.path
        if path.endswith("/public/recordings"):
            limit = min(int(request.url.params.get("limit", 20)), page_size_cap)
            page = int(request.url.params.get("page", 1))
            items = sorted(recordings.values(), key=lambda r: r["recording_at"], reverse=True)
            light = [{k: v for k, v in r.items() if k not in ("transcript", "summarizations")} for r in items]
            chunk = light[(page - 1) * limit : page * limit]
            total = len(light)
            return httpx.Response(200, json={
                "success": True,
                "data": chunk,
                "pagination": {"page": page, "limit": limit, "total": total,
                                "total_pages": -(-total // limit), "has_more": page * limit < total},
            })
        rid = path.rsplit("/", 1)[-1]
        if rid in recordings:
            return httpx.Response(200, json={"success": True, "data": recordings[rid]})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    transport.calls = calls
    return transport


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("POCKET_BRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(
        pocket_api_key="pk_test",
        data_dir=str(tmp_path / "data"),
        clients=[
            Client(name="Acme", keywords=["Jan"]),
            Client(name="Betafabriek", keywords=["Beta BV"]),
        ],
    )
    save_settings(s)
    return s


def dump(obj):
    return json.dumps(obj, indent=1)
