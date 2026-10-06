"""Client for the Pocket Public API (https://docs.heypocketai.com/docs/api).

Pocket's API is young and its response shapes have shifted over time, so every
parser here is deliberately tolerant: it accepts the documented shape and the
common variants, and never crashes on a missing field.
"""

from __future__ import annotations

import re
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
    actions_completed: set[str] = field(default_factory=set)  # ticked off in Pocket
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


_SPEAKER_KEYS = (
    "speaker", "speaker_name", "speakerName", "speaker_label", "speakerLabel",
    "speaker_id", "speakerId", "speaker_tag", "speakerTag", "participant",
)
_GENERIC_SPEAKER = re.compile(r"^(?:speaker|spk|spreker)[ _-]?(\d+)$", re.I)


def _speaker_of(item: dict) -> Any:
    for key in _SPEAKER_KEYS:
        val = item.get(key)
        if isinstance(val, dict):
            val = val.get("name") or val.get("display_name") or val.get("displayName") or val.get("label") or val.get("id")
        if val is not None and val != "" and not isinstance(val, bool):
            return val
    return ""


def _parse_segments(transcript: Any) -> tuple[list[Segment], str]:
    if transcript is None:
        return [], ""
    if isinstance(transcript, str):
        return [], transcript
    if isinstance(transcript, dict):
        for key in ("segments", "transcriptSegments", "transcript_segments", "utterances", "items"):
            if isinstance(transcript.get(key), list):
                segs, _ = _parse_segments(transcript[key])
                return segs, _first_str(transcript, "text", "full_text", "content")
        for key in ("transcription", "transcript"):  # Pocket's app API nests it one level deeper
            if isinstance(transcript.get(key), (dict, list)):
                return _parse_segments(transcript[key])
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
                segs.append(
                    Segment(
                        text=text.strip(),
                        speaker=_speaker_of(item),
                        start=_seconds(item.get("start", item.get("start_time", item.get("startTime")))),
                        end=_seconds(item.get("end", item.get("end_time", item.get("endTime")))),
                    )
                )
        return segs, ""
    return [], ""


def _speaker_names(d: dict) -> dict[str, str]:
    """Pocket can list the speakers separately ({id, name}); segments then refer to them by id."""
    names: dict[str, str] = {}
    sources = [d.get("speakers")]
    for key in ("transcript", "transcription"):
        if isinstance(d.get(key), dict):
            sources.append(d[key].get("speakers"))
    for src in sources:
        items = src.items() if isinstance(src, dict) else enumerate(src) if isinstance(src, list) else []
        for key, val in items:
            if isinstance(val, dict):
                sid = val.get("id", val.get("speaker_id", val.get("speakerId", val.get("label", key))))
                name = val.get("name") or val.get("display_name") or val.get("displayName") or ""
            else:
                sid, name = key, val
            if isinstance(name, str) and name.strip() and sid is not None:
                names[str(sid)] = name.strip()
    return names


def _label_speakers(segments: list[Segment], names: dict[str, str]) -> None:
    """Turn ids, numbers and SPEAKER_00 into readable labels, in place."""
    raw = [s.speaker for s in segments if s.speaker != ""]
    numbers = []
    for v in raw:
        m = _GENERIC_SPEAKER.match(str(v)) if not isinstance(v, (int, float)) else None
        if isinstance(v, (int, float)) or str(v).isdigit():
            numbers.append(int(v))
        elif m:
            numbers.append(int(m.group(1)))
    offset = 1 if numbers and min(numbers) == 0 else 0  # 0-based ids become Speaker 1, 2, ...
    for s in segments:
        v = s.speaker
        if v == "":
            continue
        key = str(v).strip()
        if key in names:
            s.speaker = names[key]
        elif isinstance(v, (int, float)) or key.isdigit():
            s.speaker = f"Speaker {int(v) + offset}"
        elif (m := _GENERIC_SPEAKER.match(key)) and (offset or "_" in key or key.lower().startswith("spk")):
            s.speaker = f"Speaker {int(m.group(1)) + offset}"
        else:
            s.speaker = key


# "Speaker 1: tekst", "[00:01:02] Ian: tekst", "Ian (00:01): tekst"
_TS = r"[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?"
_INLINE_TURN = re.compile(rf"^\s*(?:(?P<ts1>{_TS})\s*[-–]?\s*)?(?P<name>[^\W\d_][\w.'’-]*(?: [\w.'’-]+){{0,3}}?)\s*(?P<ts2>{_TS})?\s*:\s+(?P<text>\S.*)$")
# "Speaker 1  00:01:02" on its own line, followed by what was said
_HEADER_TURN = re.compile(rf"^\s*(?P<name>[^\W\d_][\w.'’-]*(?: [\w.'’-]+){{0,3}}?)\s+(?P<ts>{_TS})\s*$")


def _clock(ts: str | None) -> float | None:
    if not ts:
        return None
    parts = [int(x) for x in re.findall(r"\d+", ts)]
    if len(parts) == 2:
        parts = [0, *parts]
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2]) if len(parts) == 3 else None


_GENERIC_NAME = re.compile(r"^(?:unknown |onbekende )?(?:speaker|spk|spreker)[ _-]?\d+$", re.I)


def _established(names: list[str], known: set[str]) -> set[str]:
    """Names that are really speakers: they come back, look like Pocket's labels, or Pocket listed them.
    A one-off "Todo:" or "Agenda maandag 09:00" in a memo is not a speaker."""
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return {n for n, c in counts.items() if c >= 2 or _GENERIC_NAME.match(n) or n in known}


def _segments_from_text(text: str, known: set[str] = frozenset()) -> list[Segment]:
    """Recover speaker turns from a plain-text transcript, only when the pattern is clear: at least two
    established speakers, and their lines make up a real share of the text."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    inline = [_INLINE_TURN.match(ln) for ln in lines]
    names = _established([m.group("name").strip() for m in inline if m], known)
    turns = [bool(m and m.group("name").strip() in names) for m in inline]
    if len(names) >= 2 and sum(turns) >= 0.5 * len(lines):
        segs: list[Segment] = []
        for ln, m, turn in zip(lines, inline, turns):
            if turn:
                segs.append(Segment(text=m.group("text").strip(), speaker=m.group("name").strip(), start=_clock(m.group("ts1") or m.group("ts2"))))
            elif segs:
                segs[-1].text += "\n" + ln  # "Punt een: de begroting." stays part of the turn
            else:
                segs.append(Segment(text=ln))
        return segs
    headers = [_HEADER_TURN.match(ln) for ln in lines]
    names = _established([m.group("name").strip() for m in headers if m], known)
    heads = [i for i, m in enumerate(headers) if m and m.group("name").strip() in names]
    starts = [_clock(headers[i].group("ts")) or 0 for i in heads]
    followed = all(i + 1 < len(lines) and i + 1 not in heads for i in heads)
    if len(names) >= 2 and len(heads) >= 0.25 * len(lines) and followed and starts == sorted(starts):
        segs = []
        for i, ln in enumerate(lines):
            if i in heads:
                segs.append(Segment(text="", speaker=headers[i].group("name").strip(), start=_clock(headers[i].group("ts"))))
            elif segs:
                segs[-1].text = (segs[-1].text + "\n" + ln).strip()
            else:
                segs.append(Segment(text=ln))
        return [s for s in segs if s.text]
    return []


def _pick_transcript(d: dict) -> tuple[list[Segment], str]:
    """Pocket has sent the transcript under several names; take the richest version."""
    found: list[tuple[list[Segment], str]] = []
    for key in ("transcriptSegments", "transcript_segments", "transcript", "raw_transcript", "transcription", "segments", "utterances"):
        if d.get(key) not in (None, "", [], {}):
            found.append(_parse_segments(d[key]))
    with_speakers = [f for f in found if any(s.speaker != "" for s in f[0])]
    with_segments = [f for f in found if f[0]]
    segments, _ = (with_speakers or with_segments or [([], "")])[0]
    text = next((t for _, t in found if t.strip()), "")
    names = _speaker_names(d)
    if segments:
        _label_speakers(segments, names)
        if any(s.speaker for s in segments):
            return segments, ""
        recovered = _segments_from_text(text, set(names.values())) if text else []
        return (recovered, "") if recovered else (segments, "")
    recovered = _segments_from_text(text, set(names.values()))
    return (recovered, "") if recovered else ([], text)


def legacy_speaker_labels(d: dict) -> dict[str, str]:
    """Version 1.0.0 printed speakers as Pocket sent them (speaker 0 dropped, "SPEAKER_00" as is), and names
    given to speakers are stored under those labels. Maps each 1.0.0 label to today's label for the same
    person, so a name stays with the person it was given to."""
    src = d.get("transcript", d.get("transcriptSegments"))  # the source 1.0.0 read
    if isinstance(src, dict):
        src = next((src[k] for k in ("segments", "transcriptSegments", "utterances", "items") if isinstance(src.get(k), list)), None)
    if not isinstance(src, list):
        return {}
    items = [it for it in src if isinstance(it, dict) and _first_str(it, "text", "content", "transcript")]
    segs = [Segment(text="", speaker=_speaker_of(it)) for it in items]
    _label_speakers(segs, _speaker_names(d))
    out: dict[str, str] = {}
    for it, seg in zip(items, segs):
        old = it.get("speaker") or it.get("speaker_name") or it.get("speakerName") or ""
        if isinstance(old, dict):
            old = old.get("name") or old.get("label") or ""
        if old and seg.speaker:
            out.setdefault(str(old), str(seg.speaker))
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _seconds(v: Any) -> float | None:
    if isinstance(v, str) and ":" in v:
        return _clock(v)
    return _num(v)


def _action_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        text = _first_str(item, "title", "label", "text", "description", "content", "task", "name", "action")
        owner = item.get("assignee") or item.get("owner") or ""
        if isinstance(owner, dict):
            owner = owner.get("name") or owner.get("display_name") or owner.get("displayName") or ""
        due = _first_str(item, "due_date", "dueDate", "due")
        due = due[:10] if re.match(r"\d{4}-\d{2}-\d{2}T", due) else due
        extra = ", ".join(x for x in (str(owner).strip(), due) if x)
        text = " ".join(text.split())
        return f"{text} ({extra})" if text and extra else text
    return ""


def _action_done(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if any(item.get(k) is True for k in ("isCompleted", "is_completed", "completed", "done")):
        return True
    return str(item.get("status") or "").lower() in ("done", "completed", "complete", "closed")


def _action_list(val: Any) -> list:
    """Pocket's v2 summaries wrap the list: {"actionItems": {"actionItems": [...]}}."""
    for _ in range(3):
        if not isinstance(val, dict):
            break
        val = next((val[k] for k in ("actionItems", "action_items", "items", "actions", "tasks") if isinstance(val.get(k), (list, dict))), [])
    return val if isinstance(val, list) else []


def _collect_actions(items: list, actions: list[str], done: set[str]) -> None:
    for item in items:
        text = _action_text(item)
        if not text:
            continue
        actions.append(text)
        if _action_done(item):
            done.add(text)
        for key in ("subtasks", "subTasks", "children"):
            if isinstance(item, dict) and isinstance(item.get(key), list):
                _collect_actions(item[key], actions, done)


def _parse_summaries(raw: Any) -> tuple[str, list[str], set[str]]:
    """Return (summary markdown, action items, completed action items) from Pocket's summarizations field."""
    if raw is None:
        return "", [], set()
    entries: list[Any]
    fallback: Any = None
    if isinstance(raw, dict):
        # Either a single summarization, or a map of id -> summarization
        if any(k in raw for k in ("summary", "markdown", "content", "text", "action_items", "actionItems", "v2")):
            entries = [raw]
        else:
            entries = [v for k, v in raw.items() if k != "v2_action_items"]
            fallback = raw.get("v2_action_items")  # the same items again: only when the summaries have none
    elif isinstance(raw, list):
        entries = raw
    else:
        return str(raw), [], set()

    summaries: list[str] = []
    actions: list[str] = []
    done: set[str] = set()
    for e in entries:
        if isinstance(e, str):
            summaries.append(e)
            continue
        if not isinstance(e, dict):
            continue
        status = str(e.get("processingStatus") or e.get("processing_status") or "").lower()
        if status and status not in ("completed", "complete", "done", "success", "succeeded"):
            continue  # still being written in Pocket, or failed
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
            if key in inner:
                _collect_actions(_action_list(inner[key]), actions, done)
    if not actions and fallback is not None:
        _collect_actions(_action_list(fallback), actions, done)
    # De-duplicate while keeping order
    return "\n\n".join(dict.fromkeys(summaries)), list(dict.fromkeys(actions)), done


def parse_recording(d: dict) -> Recording:
    segments, text = _pick_transcript(d)
    summary, actions, done = _parse_summaries(d.get("summarizations", d.get("summary")))
    if not summary and d.get("summarizations") and d.get("summary"):
        summary = _parse_summaries(d.get("summary"))[0]
    for key in ("action_items", "actionItems"):
        if key in d:
            _collect_actions(_action_list(d[key]), actions, done)
    actions = list(dict.fromkeys(actions))
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
    starts = [s.start for s in segments if s.start is not None]
    if duration and starts and max(starts) > duration * 20:  # segment times in milliseconds
        for s in segments:
            s.start = s.start / 1000 if s.start is not None else None
            s.end = s.end / 1000 if s.end is not None else None
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
        action_items=actions,
        actions_completed=done & set(actions),
        raw=d,
    )
