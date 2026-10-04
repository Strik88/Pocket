"""SQLite full-text index over the Markdown files.

The Markdown files are the source of truth; this index is a cache that is
rebuilt incrementally from them (by mtime) and can always be deleted.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .storage import client_from_path, iter_markdown, meta_dir, parse_markdown

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
  pocket_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  mtime REAL NOT NULL,
  client TEXT,
  title TEXT,
  date TEXT,
  duration_minutes INTEGER,
  tags TEXT,
  summary TEXT,
  open_actions INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  pocket_id UNINDEXED, title, client, summary, transcript,
  tokenize = 'unicode61 remove_diacritics 2'
);
"""


@dataclass
class Row:
    pocket_id: str
    path: str
    client: str | None
    title: str
    date: str
    duration_minutes: int | None
    tags: str
    summary: str
    open_actions: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Index:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = meta_dir(settings) / "index.db"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with _lock:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- Writing --------------------------------------------------------------

    def upsert_file(self, path: Path) -> str | None:
        parsed = parse_markdown(path)
        if not parsed:
            return None
        pid = str(parsed.meta["pocket_id"])
        client = client_from_path(self.settings, path)
        tags = parsed.meta.get("tags") or []
        with _lock, self.conn:
            self.conn.execute("DELETE FROM fts WHERE pocket_id = ?", (pid,))
            self.conn.execute(
                """INSERT INTO recordings (pocket_id, path, mtime, client, title, date, duration_minutes, tags, summary, open_actions)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(pocket_id) DO UPDATE SET path=excluded.path, mtime=excluded.mtime, client=excluded.client,
                     title=excluded.title, date=excluded.date, duration_minutes=excluded.duration_minutes,
                     tags=excluded.tags, summary=excluded.summary, open_actions=excluded.open_actions""",
                (
                    pid,
                    str(path),
                    path.stat().st_mtime,
                    client,
                    parsed.title,
                    str(parsed.meta.get("date") or ""),
                    parsed.meta.get("duration_minutes"),
                    ", ".join(tags) if isinstance(tags, list) else str(tags),
                    parsed.summary,
                    sum(1 for done, _ in parsed.action_items if not done),
                ),
            )
            self.conn.execute(
                "INSERT INTO fts (pocket_id, title, client, summary, transcript) VALUES (?,?,?,?,?)",
                (pid, parsed.title, client or "", parsed.summary, parsed.transcript),
            )
        return pid

    def remove(self, pocket_id: str) -> None:
        with _lock, self.conn:
            self.conn.execute("DELETE FROM recordings WHERE pocket_id = ?", (pocket_id,))
            self.conn.execute("DELETE FROM fts WHERE pocket_id = ?", (pocket_id,))

    def refresh(self) -> int:
        """Bring the index in line with the files on disk. Returns number of changed files."""
        known = {r["path"]: (r["pocket_id"], r["mtime"]) for r in self.conn.execute("SELECT pocket_id, path, mtime FROM recordings")}
        seen_paths: set[str] = set()
        changed = 0
        for p in iter_markdown(self.settings):
            sp = str(p)
            seen_paths.add(sp)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if sp in known and known[sp][1] == mtime:
                continue
            if self.upsert_file(p):
                changed += 1
        for sp, (pid, _) in known.items():
            if sp not in seen_paths:
                row = self.conn.execute("SELECT path FROM recordings WHERE pocket_id = ?", (pid,)).fetchone()
                if row and row["path"] == sp:  # not re-found under a new path
                    self.remove(pid)
                    changed += 1
        return changed

    # -- Reading --------------------------------------------------------------

    def _rows(self, sql: str, params: tuple = ()) -> list[Row]:
        cols = "pocket_id, path, client, title, date, duration_minutes, tags, summary, open_actions"
        return [Row(**dict(r)) for r in self.conn.execute(sql.replace("{cols}", cols), params)]

    def get(self, pocket_id: str) -> Row | None:
        rows = self._rows("SELECT {cols} FROM recordings WHERE pocket_id = ?", (pocket_id,))
        return rows[0] if rows else None

    def find(self, ref: str) -> Row | None:
        """Find by Pocket id, file path, or (unique) title fragment."""
        row = self.get(ref)
        if row:
            return row
        rows = self._rows("SELECT {cols} FROM recordings WHERE path = ? OR title = ? COLLATE NOCASE", (ref, ref))
        if rows:
            return rows[0]
        rows = self._rows("SELECT {cols} FROM recordings WHERE title LIKE ? ORDER BY date DESC", (f"%{ref}%",))
        return rows[0] if len(rows) == 1 else None

    def list(self, client: str | None = None, since: str = "", until: str = "", limit: int = 50, unsorted_only: bool = False) -> list[Row]:
        where, params = [], []
        if unsorted_only:
            where.append("client IS NULL")
        elif client:
            where.append("client = ? COLLATE NOCASE")
            params.append(client)
        if since:
            where.append("date >= ?")
            params.append(since)
        if until:
            where.append("date <= ?")
            params.append(until + "T99")
        sql = "SELECT {cols} FROM recordings" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        return self._rows(sql, tuple(params))

    def search(self, query: str, client: str | None = None, limit: int = 10, any_word: bool = False) -> list[dict]:
        fts_q = to_fts_query(query, any_word=any_word)
        if not fts_q:
            return []
        sql = """SELECT r.pocket_id, r.path, r.client, r.title, r.date,
                        snippet(fts, 4, '**', '**', ' … ', 24) AS snippet
                 FROM fts JOIN recordings r ON r.pocket_id = fts.pocket_id
                 WHERE fts MATCH ?"""
        params: list = [fts_q]
        if client:
            sql += " AND r.client = ? COLLATE NOCASE"
            params.append(client)
        sql += " ORDER BY bm25(fts, 0, 5.0, 3.0, 2.0, 1.0) LIMIT ?"
        params.append(limit)
        try:
            return [dict(r) for r in self.conn.execute(sql, tuple(params))]
        except sqlite3.OperationalError:
            return []

    def client_stats(self) -> list[dict]:
        sql = """SELECT client, COUNT(*) AS recordings, MAX(date) AS last_date, SUM(open_actions) AS open_actions
                 FROM recordings GROUP BY client ORDER BY client IS NULL, last_date DESC"""
        return [dict(r) for r in self.conn.execute(sql)]


_STOP = set(
    """de het een en of in op aan van voor met dat die dit wat wie waar wanneer hoe is was zijn ik je jij we wij
    zij ze hij heeft hebben er te om over bij naar als ook nog al niet geen maar dan mijn onze jullie kan kun
    the a an and or in on at of for with that this what who where when how is was are i you we they he she it
    has have there to about by as also not no but then my our your can could would should do does did""".split()
)


def to_fts_query(text: str, any_word: bool = False) -> str:
    """Turn free text into a safe FTS5 query. Supports "quoted phrases"."""
    phrases = re.findall(r'"([^"]+)"', text)
    rest = re.sub(r'"[^"]+"', " ", text)
    words = [w for w in re.findall(r"[\w'-]+", rest.lower()) if len(w) > 1 and w not in _STOP]
    terms = [f'"{p.replace(chr(34), "")}"' for p in phrases] + [f'"{w}"*' for w in words]
    return (" OR " if any_word else " ").join(terms)
