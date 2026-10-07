"""Markdown files on disk: one file per recording, one folder per client (and project).

Layout (Dutch defaults):

    <root>/
      Klanten/<Client>/<YYYY>/<YYYY-MM-DD HHMM> <title>.md
      Klanten/<Client>/<Project>/<YYYY>/...            (recordings of a project)
      Klanten/<Client>/_Dossier.md                     (generated overview)
      Klanten/<Client>/_Briefings/, _Follow-ups/       (generated with Claude)
      _Ongesorteerd/<YYYY>/...
      _Weekoverzichten/2026-W40.md
      .pocket-bridge/            (index, state, raw JSON; safe to delete)

The folder a file lives in is the source of truth for its client and project,
so users can simply drag files between folders in Finder/Explorer.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .i18n import t
from .pocket_api import Recording

# How Pocket's data is read into a file. Raise it when that improves: existing files are then upgraded
# once (sync._upgrade_files), and a file carries the version it was written in ("bridge_format").
FORMAT_VERSION = 2

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_YEAR = re.compile(r"^(\d{4}|onbekend|unknown)$")
SPEAKER_LINE = re.compile(r"^\*\*(?P<name>[^*\n]+)\*\*(?P<rest>( \(\d\d:\d\d:\d\d\))?)$", re.M)
ACTION_LINE = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\] (?P<text>.+)$")


def fm_value(v) -> str:
    """A front-matter value as JSON (valid YAML). Unicode line separators are escaped too, so text from a
    calendar invite can never start a new front-matter line."""
    return json.dumps(v, ensure_ascii=False).replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def one_line(text) -> str:
    """Collapse all whitespace (including Unicode line breaks) for text written into a single Markdown line."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_name(name: str, max_len: int = 80) -> str:
    """A filename that is valid on macOS, Windows and Linux."""
    name = unicodedata.normalize("NFC", name)
    name = _INVALID.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if name.upper() in _RESERVED:
        name = f"_{name}"
    return (name[:max_len].rstrip(" .")) or "untitled"


def meta_dir(settings: Settings) -> Path:
    d = settings.root / ".pocket-bridge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def client_dir(settings: Settings, client: str | None, project: str | None = None) -> Path:
    if client:
        base = settings.root / settings.clients_dirname / safe_name(client)
        return base / safe_name(project) if project else base
    return settings.root / settings.unsorted_dirname


def location_from_path(settings: Settings, path: Path) -> tuple[str | None, str | None]:
    """(client, project) derived from where a file lives."""
    try:
        rel = path.resolve().relative_to(settings.root.resolve())
    except ValueError:
        return None, None
    parts = rel.parts
    if len(parts) < 3 or parts[0] != settings.clients_dirname:
        return None, None
    folder = parts[1]
    cfg = next((c for c in settings.clients if safe_name(c.name) == folder), None)
    client = cfg.name if cfg else folder
    project = None
    if len(parts) >= 4 and not _YEAR.match(parts[2]) and not parts[2].startswith("_"):
        project = parts[2]
        if cfg:
            project = next((p.name for p in cfg.projects if safe_name(p.name) == parts[2]), project)
    return client, project


def client_from_path(settings: Settings, path: Path) -> str | None:
    return location_from_path(settings, path)[0]


def recording_filename(rec: Recording) -> str:
    stamp = rec.recorded_at.astimezone().strftime("%Y-%m-%d %H%M") if rec.recorded_at else "0000-00-00 0000"
    return f"{stamp} {safe_name(rec.title, 70)}.md"


def year_of(rec: Recording) -> str:
    return rec.recorded_at.astimezone().strftime("%Y") if rec.recorded_at else "onbekend"


def target_path(settings: Settings, rec: Recording, client: str | None, project: str | None = None) -> Path:
    return client_dir(settings, client, project if client else None) / year_of(rec) / recording_filename(rec)


def demote_headings(markdown: str) -> str:
    """Pocket summaries contain their own '## ' headings; push them below our section level."""
    return re.sub(r"^(#{1,4}) ", lambda m: "#" * min(6, len(m.group(1)) + 2) + " ", markdown, flags=re.M)


def _fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _action_key(text: str) -> str:
    """Compare action items loosely: whitespace, case and the time part of a due date may differ."""
    return re.sub(r"(\d{4}-\d{2}-\d{2})T[^,)\s]*", r"\1", " ".join(str(text).split())).casefold()


def _split_note(key: str) -> tuple[str, set[str]]:
    """'bel petra (ian, 2026-10-01)' -> ('bel petra', {'ian', '2026-10-01'})"""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)$", key)
    if not m:
        return key, set()
    return m.group(1), {f.strip() for f in m.group(2).split(",") if f.strip()}


def still_ticked(items: list[str], old: list[tuple[bool, str]]) -> set[str]:
    """Which new action item texts were ticked off in the old file, also when Pocket's wording shifted
    slightly: "Bel  Petra" -> "Bel Petra", a due date without the time, an owner or date added in
    parentheses. An item that matches one left open stays open."""
    if not any(done for done, _ in old):
        return set()
    done_exact = {text for done, text in old if done}
    done_keys = {_action_key(text) for done, text in old if done}
    open_keys = {_action_key(text) for done, text in old if not done}
    old_split = [(done, *_split_note(_action_key(text))) for done, text in old]
    new_split = [_split_note(_action_key(a)) for a in items]
    out = set()
    for a, (title, fields) in zip(items, new_split):
        key = _action_key(a)
        if a in done_exact or (key in done_keys and key not in open_keys):
            out.add(a)
            continue
        same_old = [o for o in old_split if o[1] == title]
        same_new = [n for n in new_split if n[0] == title]
        if key in open_keys or len(same_old) != 1 or len(same_new) != 1:
            continue
        done, _, old_fields = same_old[0]
        if done and old_fields <= fields:  # only details were added, e.g. an owner or a date
            out.add(a)
    return out


def render_markdown(
    settings: Settings,
    rec: Recording,
    client: str | None,
    client_source: str,
    project: str | None = None,
    meeting: dict | None = None,
    speakers: dict[str, str] | None = None,
    done_actions: set[str] | list[tuple[bool, str]] | None = None,
) -> str:
    """meeting: Event.as_dict(); speakers: {"Speaker 1": "Jan"}; done_actions: the old file's action items as
    (done, text) pairs, or just the texts already ticked off."""
    lang = settings.language
    speakers = speakers or {}
    old = [(True, a) for a in done_actions] if isinstance(done_actions, (set, frozenset)) else list(done_actions or [])
    done_actions = still_ticked(rec.action_items, old) | rec.actions_completed
    fm = {
        "pocket_id": rec.id,
        "title": one_line(rec.title),
        "date": rec.recorded_at.astimezone().isoformat(timespec="minutes") if rec.recorded_at else "",
        "duration_minutes": round(rec.duration_seconds / 60) if rec.duration_seconds else None,
        "client": client or "",
        "project": project or "",
        "client_source": client_source,
        "meeting": one_line((meeting or {}).get("title", "")),
        "attendees": [one_line(a) for a in (meeting or {}).get("attendees") or []],
        "tags": rec.tags,
        "recorded_by": rec.recorded_by,
        "language": rec.language,
        "pocket_updated_at": rec.updated_at,
        "source": "pocket",
        "bridge_format": FORMAT_VERSION,
    }
    lines = ["---"]
    for k, v in fm.items():
        if (v is None or v == "" or v == []) and k not in ("client", "project"):  # these two are edited later
            continue
        lines.append(f"{k}: {fm_value(v)}")  # JSON scalars/lists are valid YAML
    lines.append("---")
    lines.append(f"# {one_line(rec.title)}")
    lines.append("")
    info = []
    if rec.recorded_at:
        info.append(f"**{t(lang, 'date')}:** {rec.recorded_at.astimezone().strftime('%Y-%m-%d %H:%M')}")
    if rec.duration_seconds:
        info.append(f"**{t(lang, 'duration')}:** {round(rec.duration_seconds / 60)} min")
    info.append(f"**{t(lang, 'client')}:** {'[[' + client + ']]' if client else t(lang, 'unsorted')}")
    if project:
        info.append(f"**{t(lang, 'project')}:** {project}")
    if rec.tags:
        info.append(f"**Tags:** {', '.join(one_line(x) for x in rec.tags)}")
    lines.append(" · ".join(info))
    if meeting:
        lines.append("")
        lines.append(f"**{t(lang, 'meeting')}:** {one_line(meeting.get('title', ''))}")
        if meeting.get("attendees"):
            lines.append(f"**{t(lang, 'attendees')}:** {', '.join(one_line(a) for a in meeting['attendees'])}")
    lines.append("")
    if rec.summary:
        lines += [f"## {t(lang, 'summary')}", "", demote_headings(rec.summary.strip()), ""]
    if rec.action_items:
        lines += [f"## {t(lang, 'action_items')}", ""]
        lines += [f"- [{'x' if a in done_actions else ' '}] {a}" for a in rec.action_items]
        lines.append("")
    lines += [f"## {t(lang, 'transcript')}", ""]
    if rec.segments:
        prev_speaker = None
        for seg in rec.segments:
            if seg.speaker and seg.speaker != prev_speaker:
                ts = _fmt_ts(seg.start)
                lines.append("")
                lines.append(f"**{speakers.get(seg.speaker, seg.speaker)}**" + (f" ({ts})" if ts else ""))
                prev_speaker = seg.speaker
            lines.append(seg.text)
    else:
        lines.append(rec.transcript_text.strip() or f"_{t(lang, 'no_transcript')}_")
    lines.append("")
    return "\n".join(lines)


@dataclass
class ParsedFile:
    path: Path
    meta: dict
    title: str
    summary: str
    action_items: list[tuple[bool, str]]
    transcript: str
    body: str


_SECTION = re.compile(r"^## (.+)$", re.M)
ACTION_HEADINGS = ("actiepunten", "action items")


def split_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().split("\n"):  # not splitlines(): that also splits on U+2028 etc.
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in meta:  # the first occurrence wins
                    continue
                try:
                    meta[k] = json.loads(v)
                except json.JSONDecodeError:
                    meta[k] = v.strip("'\"")
            body = text[end + 4 :].lstrip("\n")
    return meta, body


def parse_markdown(path: Path) -> ParsedFile | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta, body = split_frontmatter(text)
    if "pocket_id" not in meta:
        return None

    sections: dict[str, str] = {}
    matches = list(_SECTION.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[m.group(1).strip().lower()] = body[m.end() : end].strip()

    def section(*names: str) -> str:
        return next((sections[n] for n in names if n in sections), "")

    actions = []
    for line in section(*ACTION_HEADINGS).splitlines():
        m = ACTION_LINE.match(line)
        if m:
            actions.append((m.group("mark").lower() == "x", m.group("text").strip()))
    return ParsedFile(
        path=path,
        meta=meta,
        title=str(meta.get("title") or path.stem),
        summary=section("samenvatting", "summary"),
        action_items=actions,
        transcript=section("transcript"),
        body=body,
    )


def set_location(path: Path, client: str | None, project: str | None, source: str) -> None:
    """Update client/project in the front matter and the visible info line."""
    text = path.read_text(encoding="utf-8")
    new_lines, in_fm, seen_project = [], False, False
    for i, line in enumerate(text.rstrip("\n").split("\n")):
        if i == 0 and line == "---":
            in_fm = True
        elif in_fm and line == "---":
            if not seen_project:
                new_lines.append(f"project: {fm_value(project or '')}")
            in_fm = False
        elif in_fm and line.startswith("client:"):
            line = f"client: {fm_value(client or '')}"
        elif in_fm and line.startswith("project:"):
            line = f"project: {fm_value(project or '')}"
            seen_project = True
        elif in_fm and line.startswith("client_source:"):
            line = f"client_source: {fm_value(one_line(source)[:300])}"
        new_lines.append(line)
    out = []
    done = False
    for line in new_lines:
        if not done and re.search(r"\*\*(?:Klant|Client):\*\* ", line):
            items = []
            for item in line.split(" · "):
                if item.startswith("**Project:**"):
                    continue
                m = re.match(r"(\*\*(Klant|Client):\*\* )", item)
                if m:
                    item = m.group(1) + (f"[[{client}]]" if client else ("Ongesorteerd" if m.group(2) == "Klant" else "Unsorted"))
                    items.append(item)
                    if project:
                        items.append(f"**Project:** {project}")
                    continue
                items.append(item)
            line = " · ".join(items)
            done = True
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# Backwards compatible name
def set_frontmatter_client(path: Path, client: str | None, source: str) -> None:
    set_location(path, client, None, source)


# -- Speakers -------------------------------------------------------------------


def speakers_in(text: str) -> list[str]:
    """Speaker labels in the transcript section, in order of first appearance."""
    _, body = split_frontmatter(text)
    idx = body.lower().find("## transcript")
    body = body[idx:] if idx >= 0 else body
    return list(dict.fromkeys(m.group("name").strip() for m in SPEAKER_LINE.finditer(body)))


def rename_speakers_in_text(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Rename speaker headings in the transcript section, in one pass (so A->B, B->C never chains)."""
    mapping = {k: v.strip() for k, v in mapping.items() if v and v.strip() and v.strip() != k}
    idx = text.lower().find("## transcript")
    if not mapping or idx < 0:
        return text, 0
    head, tail = text[:idx], text[idx:]
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        name = m.group("name").strip()
        if name in mapping:
            count += 1
            return f"**{mapping[name]}**{m.group('rest')}"
        return m.group(0)

    return head + SPEAKER_LINE.sub(repl, tail), count


def rename_speakers_in_file(path: Path, mapping: dict[str, str]) -> int:
    """Rename speaker headings in the transcript section. Returns number of headings changed."""
    text, count = rename_speakers_in_text(path.read_text(encoding="utf-8"), mapping)
    if count:
        write_text_atomic(path, text)
    return count


def stamp_format(text: str) -> str:
    """Record in the front matter that the file is in today's format."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    lines = [ln for ln in text[:end].split("\n") if not ln.startswith("bridge_format:")]
    return "\n".join(lines) + f"\nbridge_format: {FORMAT_VERSION}" + text[end:]


def file_format(meta: dict) -> int:
    try:
        return int(meta.get("bridge_format") or 1)
    except (TypeError, ValueError):
        return 1


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temporary file next to it, so a crash never leaves half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# -- Action items -----------------------------------------------------------------


def set_action_done(path: Path, text: str, done: bool) -> bool:
    """Tick or untick the action item with this text in the file's action items section."""
    lines = path.read_text(encoding="utf-8").split("\n")
    in_section, changed = False, False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            in_section = line[3:].strip().lower() in ACTION_HEADINGS
            continue
        if not in_section:
            continue
        m = ACTION_LINE.match(line)
        if m and m.group("text").strip() == text.strip():
            lines[i] = f"{m.group('indent')}- [{'x' if done else ' '}] {m.group('text')}"
            changed = True
            break
    if changed:
        path.write_text("\n".join(lines), encoding="utf-8")
    return changed


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many files named {path.name}")


def iter_markdown(settings: Settings):
    """All recording files (skips generated files and folders starting with '_')."""
    root = settings.root
    if not root.exists():
        return
    for p in root.rglob("*.md"):
        try:
            rel = p.relative_to(root).parts
        except ValueError:
            continue
        if ".pocket-bridge" in rel or p.name.startswith("_"):
            continue
        if any(part.startswith("_") and part != settings.unsorted_dirname for part in rel[:-1]):
            continue
        yield p
