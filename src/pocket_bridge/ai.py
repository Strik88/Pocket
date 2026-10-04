"""Optional Claude features: classifying a recording to a client and answering questions.

Everything here is optional: without an Anthropic API key Pocket Bridge works
fine, it just relies on the rules you set per client.
"""

from __future__ import annotations

import os

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


class Classification(BaseModel):
    client: str | None = Field(description="Exact name of an existing client, or null")
    new_client: str | None = Field(description="Name for a new client if allowed and clearly identifiable, else null")
    confidence: float = Field(description="0.0 - 1.0")
    reason: str = Field(description="One short sentence")


def classify(settings: Settings, rec: Recording) -> tuple[str | None, str]:
    """Ask Claude which client a recording belongs to. Returns (client or None, reason)."""
    client = make_client(settings)
    clients_desc = "\n".join(
        f"- {c.name}" + (f" (trefwoorden/keywords: {', '.join(c.keywords)})" if c.keywords else "") + (f" — {c.notes}" if c.notes else "")
        for c in settings.clients
    ) or "(nog geen klanten / no clients yet)"
    transcript = rec.plain_transcript()
    note = ""
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]
        note = "\n[Transcript ingekort / truncated for length]"
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
Pocket tags: {", ".join(rec.tags) or "-"}
Summary:
{rec.summary or "-"}

Transcript:
{transcript}{note}
</recording>"""
    try:
        resp = client.beta.messages.parse(
            model=settings.claude_model,
            max_tokens=2048,
            output_config={"effort": "low"},
            output_format=Classification,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic API-key ongeldig / invalid key") from exc
    except anthropic.APIError as exc:
        raise AIError(f"Claude-fout / Claude error: {exc}") from exc
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None, "Claude gaf geen classificatie / no classification"
    result = resp.parsed_output
    if result.confidence < 0.6:
        return None, f"onzeker / unsure: {result.reason}"
    if result.client:
        known = settings.find_client(result.client)
        if known:
            return known.name, result.reason
    if result.new_client and settings.ai_may_create_clients:
        return result.new_client.strip(), f"nieuwe klant / new client: {result.reason}"
    return None, result.reason


def ask(settings: Settings, question: str, sources: list[dict], history: list[dict] | None = None) -> str:
    """Answer a question using the given transcripts as context.

    sources: [{"title", "date", "client", "text"}]
    """
    client = make_client(settings)
    lang = "Dutch" if settings.language == "nl" else "English"
    docs = "\n\n".join(
        f'<document index="{i + 1}" title="{s["title"]}" date="{s.get("date", "")}" client="{s.get("client") or "-"}">\n{s["text"]}\n</document>'
        for i, s in enumerate(sources)
    )
    system = (
        "You help a consultant get information out of their recorded conversations (Pocket transcripts). "
        "Answer only from the documents provided; say so plainly when they do not contain the answer. "
        "Cite which conversation (title + date) each point comes from. "
        f"Answer in {lang} unless the user writes in another language. Use short paragraphs and lists where helpful."
    )
    messages = list(history or [])
    messages.append({"role": "user", "content": f"<documents>\n{docs}\n</documents>\n\n{question}"})
    try:
        with client.beta.messages.stream(
            model=settings.claude_model,
            max_tokens=16000,
            system=system,
            output_config={"effort": "medium"},
            betas=FALLBACK_BETAS,
            fallbacks="default",
            messages=messages,
        ) as stream:
            final = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic API-key ongeldig / invalid key") from exc
    except anthropic.APIError as exc:
        raise AIError(f"Claude-fout / Claude error: {exc}") from exc
    if final.stop_reason == "refusal":
        return "Claude kon deze vraag niet beantwoorden. / Claude declined to answer this question."
    return "".join(b.text for b in final.content if b.type == "text").strip()
