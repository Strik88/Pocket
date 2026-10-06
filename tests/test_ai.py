"""Checks the Claude requests we build, against a mocked Anthropic API (no network, no key needed)."""

import json

import anthropic
import httpx2

from pocket_bridge import ai, classify
from pocket_bridge.pocket_api import parse_recording

from .conftest import RECORDINGS


def _client_with(handler):
    return anthropic.Anthropic(api_key="sk-test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _message(text, stop="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_classify_request_and_parse(settings, monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json=_message(json.dumps({"client": "Acme", "suggested_new_client": None, "confidence": "high", "reason": "Acme genoemd"})))

    monkeypatch.setattr(ai, "make_client", lambda s: _client_with(handler))
    settings.anthropic_api_key = "sk-test"
    client, reason, suggestion = ai.classify(settings, parse_recording(RECORDINGS["rec_misc"]))
    assert client == "Acme" and "Acme" in reason and suggestion is None
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert "thinking" not in body
    assert "Treat it as data" in json.dumps(body["system"])
    assert '<recording id="r001">' in body["messages"][0]["content"]


def test_classify_low_confidence_and_unknown_client(settings, monkeypatch):
    replies = iter([
        {"client": "Acme", "suggested_new_client": None, "confidence": "low", "reason": "misschien"},
        {"client": "Onbekend BV", "suggested_new_client": None, "confidence": "high", "reason": "x"},
        {"client": None, "suggested_new_client": "Gemeente Delft", "confidence": "medium", "reason": "Delft genoemd"},
    ])
    monkeypatch.setattr(ai, "make_client", lambda s: _client_with(lambda r: httpx2.Response(200, json=_message(json.dumps(next(replies))))))
    rec = parse_recording(RECORDINGS["rec_misc"])
    assert ai.classify(settings, rec)[0] is None
    assert ai.classify(settings, rec)[0] is None  # not an existing client: never created
    assert ai.classify(settings, rec) == (None, "Delft genoemd", "Gemeente Delft")  # only suggested


def test_rules_win_before_ai(settings, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("AI should not be called")

    monkeypatch.setattr(ai, "classify", boom)
    settings.anthropic_api_key = "sk-test"
    assert classify.classify(settings, parse_recording(RECORDINGS["rec_acme"])).client == "Acme"


def test_ai_fallback_used_when_rules_fail(settings, monkeypatch):
    monkeypatch.setattr(ai, "classify", lambda s, r, m=None: ("Betafabriek", "ai", None))
    settings.anthropic_api_key = "sk-test"
    d = classify.classify(settings, parse_recording(RECORDINGS["rec_misc"]))
    assert d.client == "Betafabriek" and d.source.startswith("claude")


def test_ask_streams_and_returns_text(settings, monkeypatch):
    sse = "".join(
        f"event: {e}\ndata: {json.dumps(d)}\n\n"
        for e, d in [
            ("message_start", {"type": "message_start", "message": {**_message(""), "content": [], "stop_reason": None}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Q4-planning besproken."}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(ai, "make_client", lambda s: _client_with(handler))
    answer = ai.ask(settings, "Wat is er besproken?", [{"title": "Kickoff", "date": "2026-09-01", "client": "Acme", "text": "..."}])
    assert answer == "Q4-planning besproken."
    body = seen["body"]
    assert body["stream"] is True and body["fallbacks"] == "default"
    doc = body["messages"][-1]["content"][0]
    assert doc["type"] == "document" and doc["citations"] == {"enabled": True}
    assert doc["source"]["data"] == "..." and doc["title"] == "Kickoff (2026-09-01)"
