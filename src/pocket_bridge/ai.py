"""Optional Claude features.

Everything here is optional: without an Anthropic API key Pocket Bridge works
fine, it just relies on the rules you set per client. In Claude Desktop the
same work is done by Claude itself through the MCP tools.
"""

from __future__ import annotations

import os
from typing import Iterator

import anthropic
from pydantic import BaseModel, Field

from .config import Settings
from .pocket_api import Recording

# Opt into server-side refusal fallback: if a request is declined by a safety
# classifier, the API re-runs it on Anthropic's recommended fallback model.
FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]
MAX_TRANSCRIPT_CHARS = 400_000  # ~100k tokens; plenty for a multi-hour meeting


class AIError(Exception):
    pass


def make_client(settings: Settings) -> anthropic.Anthropic:
    key = settings.anthropic_api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise AIError("Geen Anthropic API-key ingesteld / no Anthropic API key configured")
    return anthropic.Anthropic(api_key=key)


def _lang(settings: Settings) -> str:
    return "Dutch" if settings.language == "nl" else "English"


def _clip(text: str) -> str:
    if len(text) > MAX_TRANSCRIPT_CHARS:
        return text[:MAX_TRANSCRIPT_CHARS] + "\n[Ingekort / truncated for length]"
    return text


def _call_text(settings: Settings, system: str, prompt: str, effort: str = "medium") -> str:
    """One streamed request (long inputs), returns the text."""
    client = make_client(settings)
    try:
        with client.beta.messages.stream(
            model=settings.claude_model,
            max_tokens=16000,
            system=system,
            output_config={"effort": effort},
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            final = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic API-key ongeldig / invalid key") from exc
    except anthropic.APIError as exc:
        raise AIError(f"Claude-fout / Claude error: {exc}") from exc
    if final.stop_reason == "refusal":
        raise AIError("Claude weigerde dit verzoek / Claude declined this request")
    return "".join(b.text for b in final.content if b.type == "text").strip()


def _call_parsed(settings: Settings, prompt: str, schema: type[BaseModel], effort: str = "low", max_tokens: int = 4096):
    client = make_client(settings)
    try:
        resp = client.beta.messages.parse(
            model=settings.claude_model,
            max_tokens=max_tokens,
            output_config={"effort": effort},
            output_format=schema,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic API-key ongeldig / invalid key") from exc
    except anthropic.APIError as exc:
        raise AIError(f"Claude-fout / Claude error: {exc}") from exc
    if resp.stop_reason == "refusal":
        return None
    return resp.parsed_output


def check_key(settings: Settings) -> str:
    client = make_client(settings)
    resp = client.beta.messages.create(
        model=settings.claude_model,
        max_tokens=64,
        output_config={"effort": "low"},
        betas=FALLBACK_BETAS,
        fallbacks="default",
        messages=[{"role": "user", "content": "Reply with just: OK"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip() or "OK"


def _meeting_text(meeting) -> str:
    if not meeting:
        return "-"
    return f"{meeting.title} (attendees: {', '.join(meeting.attendees) or '-'})"


# -- Classification ---------------------------------------------------------------


class Classification(BaseModel):
    client: str | None = Field(description="Exact name of an existing client, or null")
    new_client: str | None = Field(description="Name for a new client if allowed and clearly identifiable, else null")
    confidence: float = Field(description="0.0 - 1.0")
    reason: str = Field(description="One short sentence")


def classify(settings: Settings, rec: Recording, meeting=None) -> tuple[str | None, str]:
    """Ask Claude which client a recording belongs to. Returns (client or None, reason)."""
    clients_desc = "\n".join(
        f"- {c.name}"
        + (f" (keywords: {', '.join(c.keywords)})" if c.keywords else "")
        + (f" (e-mail domains: {', '.join(c.email_domains)})" if c.email_domains else "")
        + (f" — {c.notes}" if c.notes else "")
        for c in settings.clients
    ) or "(no clients yet)"
    new_rule = (
        "If the recording clearly concerns a client/organisation that is NOT in the list, put its name in new_client."
        if settings.ai_may_create_clients
        else "Never invent new clients: new_client must be null."
    )
    prompt = f"""You sort meeting recordings into client folders for a consultant.

Existing clients:
{clients_desc}

Decide which client this recording is about. Choose an existing client only when the recording is clearly about that client (names, organisation, project). Internal meetings, personal notes or unclear recordings get client = null. {new_rule} Use confidence below 0.6 when unsure.

<recording>
Title: {rec.title}
Calendar meeting: {_meeting_text(meeting)}
Pocket tags: {", ".join(rec.tags) or "-"}
Summary:
{rec.summary or "-"}

Transcript:
{_clip(rec.plain_transcript())}
</recording>"""
    result = _call_parsed(settings, prompt, Classification, max_tokens=2048)
    if result is None:
        return None, "Claude gaf geen classificatie / no classification"
    if result.confidence < 0.6:
        return None, f"onzeker / unsure: {result.reason}"
    if result.client:
        known = settings.find_client(result.client)
        if known:
            return known.name, result.reason
    if result.new_client and settings.ai_may_create_clients:
        return result.new_client.strip(), f"nieuwe klant / new client: {result.reason}"
    return None, result.reason


# -- Speakers ---------------------------------------------------------------------


class SpeakerName(BaseModel):
    label: str = Field(description="The label as it appears in the transcript, e.g. 'Speaker 1'")
    name: str | None = Field(description="Real name, or null when it cannot be determined")


class SpeakerGuess(BaseModel):
    speakers: list[SpeakerName]


def suggest_speakers(settings: Settings, transcript: str, labels: list[str], attendees: list[str], owner: str = "") -> dict[str, str]:
    prompt = f"""Below is a meeting transcript with generic speaker labels. Work out the real name behind each label from how people address each other, introductions and context.
Only give a name when the transcript makes it reasonably clear; otherwise null.
The person who made the recording is: {owner or "unknown"}.
Calendar attendees (may help): {", ".join(attendees) or "-"}
Labels: {", ".join(labels)}

<transcript>
{_clip(transcript)}
</transcript>"""
    result = _call_parsed(settings, prompt, SpeakerGuess, effort="medium")
    if not result:
        return {}
    return {s.label: s.name for s in result.speakers if s.name and s.label in labels}


# -- Reports ----------------------------------------------------------------------


def briefing(settings: Settings, client: str, dossier: str, transcripts: list[tuple[str, str]]) -> str:
    """Meeting prep. transcripts: [(title + date, markdown)] newest first."""
    docs = "\n\n".join(f'<conversation name="{n}">\n{_clip(t)}\n</conversation>' for n, t in transcripts)
    system = f"You are a sharp, practical assistant to a consultant. Write in {_lang(settings)}. Be concrete; no filler."
    prompt = f"""Prepare me for my next meeting with {client}.

<dossier>
{dossier}
</dossier>

{docs}

Write a briefing in Markdown with these sections:
1. Situation in 3-5 bullets (where things stand, latest decisions)
2. Open action items (ours and theirs), with who and when if known
3. People involved and what matters to them
4. Risks or sensitivities to keep in mind
5. Three suggested goals or questions for the next conversation
Name the conversation (title and date) behind each important point."""
    return _call_text(settings, system, prompt)


class FollowUp(BaseModel):
    subject: str
    body: str = Field(description="Plain-text e-mail body, ready to send, with greeting and sign-off")


def followup(settings: Settings, markdown: str, attendees: list[str], sender: str = "") -> FollowUp | None:
    prompt = f"""Write a follow-up e-mail after this conversation, in {_lang(settings)}.
Tone: short, friendly, concrete. Structure: thanks, short recap, agreements/decisions, action items with owners, next step.
Only include what is in the conversation. Sign off as: {sender or "[naam]"}.
Recipients (from the calendar, may be empty): {", ".join(attendees) or "-"}

<conversation>
{_clip(markdown)}
</conversation>"""
    return _call_parsed(settings, prompt, FollowUp, effort="medium", max_tokens=8000)


def weekly_summary(settings: Settings, overview_markdown: str) -> str:
    system = f"You write concise weekly reviews for a consultant. Write in {_lang(settings)}."
    prompt = f"""Here is the overview of all my recorded conversations of one week, per client.

{_clip(overview_markdown)}

Write a short weekly review in Markdown (max ~250 words): the main developments per client, decisions made, what needs attention next week. No introduction."""
    return _call_text(settings, system, prompt, effort="low")


def client_status(settings: Settings, client: str, transcripts: list[tuple[str, str]], previous: str = "") -> str:
    docs = "\n\n".join(f'<conversation name="{n}">\n{_clip(t)}\n</conversation>' for n, t in transcripts)
    system = f"You maintain a living status note per client for a consultant. Write in {_lang(settings)}."
    prompt = f"""Update the status note for client {client}.

<previous_status>
{previous or "-"}
</previous_status>

Most recent conversations:
{docs}

Write max ~150 words of Markdown bullets: current phase, latest decisions, open questions, next step. No heading."""
    return _call_text(settings, system, prompt, effort="low")


# -- Questions with citations -------------------------------------------------------


def _ask_messages(question: str, sources: list[dict], history: list[dict] | None) -> list[dict]:
    content: list[dict] = [
        {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": s["text"]},
            "title": f'{s["title"]} ({s.get("date", "")})',
            "context": f'Client: {s.get("client") or "-"}',
            "citations": {"enabled": True},
        }
        for s in sources
    ]
    content.append({"type": "text", "text": question})
    return [*(history or []), {"role": "user", "content": content}]


def _system(settings: Settings) -> str:
    return (
        "You help a consultant get information out of their recorded conversations (Pocket transcripts). "
        "Answer only from the documents provided; say so plainly when they do not contain the answer. "
        f"Answer in {_lang(settings)} unless the user writes in another language. Use short paragraphs and lists where helpful."
    )


def ask_stream(settings: Settings, question: str, sources: list[dict], history: list[dict] | None = None) -> Iterator[dict]:
    """Yields {"type": "text", "text"} while streaming, then {"type": "done", "blocks": [...]}
    where blocks are [{"text", "citations": [{"source", "cited_text"}]}] (source = index into sources)."""
    client = make_client(settings)
    try:
        with client.beta.messages.stream(
            model=settings.claude_model,
            max_tokens=16000,
            system=_system(settings),
            output_config={"effort": "medium"},
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=_ask_messages(question, sources, history),
        ) as stream:
            for text in stream.text_stream:
                yield {"type": "text", "text": text}
            final = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic API-key ongeldig / invalid key") from exc
    except anthropic.APIError as exc:
        raise AIError(f"Claude-fout / Claude error: {exc}") from exc
    if final.stop_reason == "refusal":
        yield {"type": "done", "blocks": [{"text": "Claude kon deze vraag niet beantwoorden. / Claude declined to answer.", "citations": []}]}
        return
    blocks = []
    for b in final.content:
        if b.type != "text":
            continue
        cites = []
        for c in b.citations or []:
            idx = getattr(c, "document_index", None)
            if idx is not None:
                cites.append({"source": idx, "cited_text": getattr(c, "cited_text", "")})
        blocks.append({"text": b.text, "citations": cites})
    yield {"type": "done", "blocks": blocks}


def ask(settings: Settings, question: str, sources: list[dict], history: list[dict] | None = None) -> str:
    """Non-streaming convenience wrapper: answer text with [n] markers for citations."""
    blocks: list[dict] = []
    for event in ask_stream(settings, question, sources, history):
        if event["type"] == "done":
            blocks = event["blocks"]
    out = []
    for b in blocks:
        out.append(b["text"])
        refs = sorted({c["source"] + 1 for c in b["citations"]})
        if refs:
            out.append("".join(f"[{r}]" for r in refs))
    return "".join(out).strip()
