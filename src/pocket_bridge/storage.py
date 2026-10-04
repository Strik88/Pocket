"""Markdown files on disk: one file per recording, one folder per client.

Layout (Dutch defaults):

    <root>/
      Klanten/<Client>/<YYYY>/<YYYY-MM-DD HHMM> <title>.md
      Klanten/<Client>/_Dossier.md
      _Ongesorteerd/<YYYY>/...
      .pocket-bridge/            (index, state, raw JSON; safe to delete)

The folder a file lives in is the source of truth for its client, so users can
simply drag files between client folders in Finder/Explorer.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .i18n import t
from .pocket_api import Recording

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


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


def client_dir(settings: Settings, client: str | None) -> Path:
    if client:
        return settings.root / settings.clients_dirname / safe_name(client)
    return settings.root / settings.unsorted_dirname


def client_from_path(settings: Settings, path: Path) -> str | None:
    """Derive the client from where a file lives."""
    try:
        rel = path.resolve().relative_to(settings.root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == settings.clients_dirname:
        folder = parts[1]
        known = next((c.name for c in settings.clients if safe_name(c.name) == folder), None)
        return known or folder
    return None


def recording_filename(rec: Recording) -> str:
    stamp = rec.recorded_at.astimezone().strftime("%Y-%m-%d %H%M") if rec.recorded_at else "0000-00-00 0000"
    return f"{stamp} {safe_name(rec.title, 70)}.md"


def target_path(settings: Settings, rec: Recording, client: str | None) -> Path:
    year = rec.recorded_at.astimezone().strftime("%Y") if rec.recorded_at else "onbekend"
    return client_dir(settings, client) / year / recording_filename(rec)


def _fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_markdown(settings: Settings, rec: Recording, client: str | None, client_source: str) -> str:
    lang = settings.language
    fm = {
        "pocket_id": rec.id,
        "title": rec.title,
        "date": rec.recorded_at.astimezone().isoformat(timespec="minutes") if rec.recorded_at else "",
        "duration_minutes": round(rec.duration_seconds / 60) if rec.duration_seconds else None,
        "client": client or "",
        "client_source": client_source,
        "tags": rec.tags,
        "recorded_by": rec.recorded_by,
        "language": rec.language,
        "pocket_updated_at": rec.updated_at,
        "source": "pocket",
    }
    lines = ["---"]
    for k, v in fm.items():
        if (v is None or v == "") and k != "client":  # always keep client: it is edited later
            continue
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")  # JSON scalars/lists are valid YAML
    lines.append("---")
    lines.append(f"# {rec.title}")
    lines.append("")
    info = []
    if rec.recorded_at:
        info.append(f"**{t(lang, 'date')}:** {rec.recorded_at.astimezone().strftime('%Y-%m-%d %H:%M')}")
    if rec.duration_seconds:
        info.append(f"**{t(lang, 'duration')}:** {round(rec.duration_seconds / 60)} min")
    info.append(f"**{t(lang, 'client')}:** {'[[' + client + ']]' if client else t(lang, 'unsorted')}")
    if rec.tags:
        info.append(f"**Tags:** {', '.join(rec.tags)}")
    lines.append(" · ".join(info))
    lines.append("")
    if rec.summary:
        lines += [f"## {t(lang, 'summary')}", "", rec.summary.strip(), ""]
    if rec.action_items:
        lines += [f"## {t(lang, 'action_items')}", ""]
        lines += [f"- [ ] {a}" for a in rec.action_items]
        lines.append("")
    lines += [f"## {t(lang, 'transcript')}", ""]
    if rec.segments:
        prev_speaker = None
        for seg in rec.segments:
            if seg.speaker and seg.speaker != prev_speaker:
                ts = _fmt_ts(seg.start)
                lines.append("")
                lines.append(f"**{seg.speaker}**" + (f" ({ts})" if ts else ""))
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


def parse_markdown(path: Path) -> ParsedFile | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                try:
                    meta[k.strip()] = json.loads(v)
                except json.JSONDecodeError:
                    meta[k.strip()] = v.strip("'\"")
            body = text[end + 4 :].lstrip("\n")
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
    for line in section("actiepunten", "action items").splitlines():
        m = re.match(r"^\s*- \[([ xX])\] (.+)$", line)
        if m:
            actions.append((m.group(1).lower() == "x", m.group(2).strip()))
    return ParsedFile(
        path=path,
        meta=meta,
        title=str(meta.get("title") or path.stem),
        summary=section("samenvatting", "summary"),
        action_items=actions,
        transcript=section("transcript"),
        body=body,
    )


def set_frontmatter_client(path: Path, client: str | None, source: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_lines = []
    in_fm = False
    for i, line in enumerate(text.splitlines()):
        if i == 0 and line == "---":
            in_fm = True
        elif in_fm and line == "---":
            in_fm = False
        elif in_fm and line.startswith("client:"):
            line = f"client: {json.dumps(client or '', ensure_ascii=False)}"
        elif in_fm and line.startswith("client_source:"):
            line = f"client_source: {json.dumps(source)}"
        new_lines.append(line)
    text = "\n".join(new_lines) + "\n"
    # Update the visible client link in the info line too
    text = re.sub(r"(\*\*(?:Klant|Client):\*\* )(\[\[[^\]]*\]\]|[^·\n]+)", lambda m: m.group(1) + (f"[[{client}]]" if client else "—"), text, count=1)
    path.write_text(text, encoding="utf-8")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many files named {path.name}")


def iter_markdown(settings: Settings):
    root = settings.root
    if not root.exists():
        return
    for p in root.rglob("*.md"):
        if ".pocket-bridge" in p.parts or p.name.startswith("_"):
            continue
        yield p
