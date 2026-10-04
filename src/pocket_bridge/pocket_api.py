"""Client for the Pocket Public API (https://docs.heypocketai.com/docs/api).

Pocket's API is young and its response shapes have shifted over time, so every
parser here is deliberately tolerant: it accepts the documented shape and the
common variants, and never crashes on a missing field.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from .config import DEFAULT_POCKET_BASE_URL


class PocketError(Exception):
    pass


class PocketAuthError(PocketError):
    pass


@dataclass
class Segment:
    text: str
    speaker: str = ""
    start: float | None = None
    end: float | None = None


@dataclass
class Recording:
    id: str
    title: str
    recorded_at: datetime | None
    updated_at: str = ""
    duration_seconds: float | None = None
    language: str = ""
    state: str = ""
    recorded_by: str = ""
    folder_id: str = ""
    tags: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    transcript_text: str = ""
    summary: str = ""
    action_items: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_transcript(self) -> bool:
        return bool(self.segments or self.transcript_text.strip())

    def plain_transcript(self) -> str:
        if self.segments:
            return "\n".join(
                f"{s.speaker}: {s.text}" if s.speaker else s.text for s in self.segments
            )
        return self.transcript_text


class PocketClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_POCKET_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PocketClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        for attempt in range(5):
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == 4:
                    raise PocketError(f"Kan Pocket niet bereiken / cannot reach Pocket: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if resp.status_code in (401, 403):
                raise PocketAuthError(
                    "Pocket weigert de API-key (401/403). Controleer de key. / Pocket rejected the API key."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("retry-after") or 2**attempt)
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 400:
                raise PocketError(f"Pocket API {resp.status_code} op {path}: {resp.text[:300]}")
            body = resp.json()
            # Some endpoints wrap everything in {"success": true, "data": ...}
            if isinstance(body, dict) and "data" in body and set(body) <= {"data", "success", "pagination", "meta"}:
                if isinstance(body["data"], dict) and "pagination" in body and "pagination" not in body["data"]:
                    return {**body["data"], "pagination": body["pagination"]}
                if isinstance(body["data"], list):
                    return {"recordings": body["data"], "pagination": body.get("pagination") or body.get("meta")}
                return body["data"]
            return body
        raise PocketError(f"Pocket API bleef fouten geven op {path} / kept failing")

    # -- Endpoints ----------------------------------------------------------

    def check(self) -> int:
        """Validate the key. Returns the total number of recordings if Pocket reports it."""
        body = self._get("/public/recordings", {"limit": 1})
        pag = _pagination(body)
        total = pag.get("total")
        return int(total) if isinstance(total, (int, float)) else len(_items(body))

    def iter_recordings(self, start_date: str = "", end_date: str = "", page_size: int = 100) -> Iterator[dict]:
        """Yield recording summaries (light objects without transcript)."""
        page, cursor = 1, ""
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": page_size, "startDate": start_date, "endDate": end_date}
            if cursor:
                params["cursor"] = cursor
            else:
                params["page"] = page
            body = self._get("/public/recordings", params)
            items = _items(body)
            new = [it for it in items if str(it.get("id")) not in seen]
            for it in new:
                seen.add(str(it.get("id")))
                yield it
            pag = _pagination(body)
            if not new:
                return
            if pag.get("next_cursor") or pag.get("nextCursor"):
                cursor = str(pag.get("next_cursor") or pag.get("nextCursor"))
                continue
            has_more = pag.get("has_more", pag.get("hasMore"))
            total_pages = pag.get("total_pages") or pag.get("totalPages")
            if has_more is False or (total_pages and page >= int(total_pages)):
                return
            if has_more is None and not total_pages and len(items) < page_size:
                return
            page += 1

    def get_recording(self, recording_id: str) -> Recording:
        body = self._get(
            f"/public/recordings/{recording_id}",
            {"include_transcript": "true", "include_summarizations": "true"},
        )
        if isinstance(body, dict) and isinstance(body.get("recording"), dict):
            merged = dict(body["recording"])
            for k, v in body.items():
                merged.setdefault(k, v)
            body = merged
        return parse_recording(body)


# -- Parsing helpers (module level so tests can use them) ---------------------


def _items(body: Any) -> list[dict]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("recordings", "data", "items", "results"):
            val = body.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                return _items(val)
    return []


def _pagination(body: Any) -> dict:
    if isinstance(body, dict):
        for key in ("pagination", "meta"):
            if isinstance(body.get(key), dict):
                return body[key]
    return {}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_str(d: dict, *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _parse_segments(transcript: Any) -> tuple[list[Segment], str]:
    if transcript is None:
        return [], ""
    if isinstance(transcript, str):
        return [], transcript
    if isinstance(transcript, dict):
        for key in ("segments", "transcriptSegments", "utterances", "items"):
            if isinstance(transcript.get(key), list):
                segs, _ = _parse_segments(transcript[key])
                return segs, _first_str(transcript, "text", "full_text", "content")
        return [], _first_str(transcript, "text", "full_text", "content", "transcript")
    if isinstance(transcript, list):
        segs = []
        for item in transcript:
            if isinstance(item, str):
                segs.append(Segment(text=item))
            elif isinstance(item, dict):
                text = _first_str(item, "text", "content", "transcript")
                if not text:
                    continue
                speaker = item.get("speaker") or item.get("speaker_name") or item.get("speakerName") or ""
                if isinstance(speaker, dict):
                    speaker = speaker.get("name") or speaker.get("label") or ""
                segs.append(
                    Segment(
                        text=text.strip(),
                        speaker=str(speaker),
                        start=_num(item.get("start", item.get("start_time", item.get("startTime")))),
                        end=_num(item.get("end", item.get("end_time", item.get("endTime")))),
                    )
                )
        return segs, ""
    return [], ""


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _action_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        text = _first_str(item, "title", "text", "description", "content", "task", "name")
        owner = _first_str(item, "assignee", "owner")
        due = _first_str(item, "due_date", "dueDate", "due")
        extra = ", ".join(x for x in (owner, due) if x)
        return f"{text} ({extra})" if text and extra else text
    return ""


def _parse_summaries(raw: Any) -> tuple[str, list[str]]:
    """Return (summary markdown, action items) from Pocket's summarizations field."""
    if raw is None:
        return "", []
    entries: list[Any]
    if isinstance(raw, dict):
        # Either a single summarization, or a map of id -> summarization
        if any(k in raw for k in ("summary", "markdown", "content", "text", "action_items", "actionItems")):
            entries = [raw]
        else:
            entries = list(raw.values())
    elif isinstance(raw, list):
        entries = raw
    else:
        return str(raw), []

    summaries: list[str] = []
    actions: list[str] = []
    for e in entries:
        if isinstance(e, str):
            summaries.append(e)
            continue
        if not isinstance(e, dict):
            continue
        inner = e.get("v2") if isinstance(e.get("v2"), dict) else e
        text = ""
        for key in ("markdown", "summary", "content", "text", "body"):
            val = inner.get(key)
            if isinstance(val, str) and val.strip():
                text = val
                break
            if isinstance(val, dict):
                text = _first_str(val, "markdown", "text", "content", "summary")
                if text:
                    break
        if text:
            summaries.append(text.strip())
        for key in ("action_items", "actionItems", "actions", "tasks"):
            val = inner.get(key)
            if isinstance(val, dict):
                val = val.get("items") or val.get("actions") or []
            if isinstance(val, list):
                actions.extend(t for t in (_action_text(a) for a in val) if t)
    # De-duplicate while keeping order
    return "\n\n".join(dict.fromkeys(summaries)), list(dict.fromkeys(actions))


def parse_recording(d: dict) -> Recording:
    segments, text = _parse_segments(d.get("transcript", d.get("transcriptSegments")))
    summary, actions = _parse_summaries(d.get("summarizations", d.get("summary")))
    for key in ("action_items", "actionItems"):
        if isinstance(d.get(key), list):
            actions.extend(t for t in (_action_text(a) for a in d[key]) if t)
    tags = []
    for t in d.get("tags") or []:
        name = t.get("name") if isinstance(t, dict) else t
        if name:
            tags.append(str(name))
    rb = d.get("recorded_by") or d.get("recordedBy") or {}
    recorded_by = (
        (rb.get("display_name") or rb.get("name") or rb.get("email") or "") if isinstance(rb, dict) else str(rb)
    )
    duration = _num(d.get("duration", d.get("duration_seconds")))
    if duration and duration > 100_000:  # milliseconds
        duration = duration / 1000
    return Recording(
        id=str(d.get("id") or d.get("recording_id") or ""),
        title=_first_str(d, "title", "name") or "Untitled",
        recorded_at=_parse_dt(d.get("recording_at") or d.get("recorded_at") or d.get("created_at") or d.get("createdAt")),
        updated_at=str(d.get("updated_at") or d.get("updatedAt") or ""),
        duration_seconds=duration,
        language=str(d.get("language") or ""),
        state=str(d.get("state") or d.get("status") or ""),
        recorded_by=recorded_by,
        folder_id=str(d.get("folder_id") or d.get("folderId") or ""),
        tags=tags,
        segments=segments,
        transcript_text=text,
        summary=summary,
        action_items=list(dict.fromkeys(actions)),
        raw=d,
    )
