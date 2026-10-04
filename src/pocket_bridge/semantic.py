"""Search on meaning: a small multilingual embedding model that runs locally.

The model (~220 MB) is downloaded once, on first use, into the Pocket Bridge
config folder. Transcripts are split into overlapping chunks; vectors are kept
in the same SQLite file as the full-text index. Results are combined with the
keyword search (reciprocal rank fusion), so exact names still rank well.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Settings, config_dir
from .index import Index
from .storage import parse_markdown

log = logging.getLogger(__name__)

CHUNK_CHARS = 900
OVERLAP_CHARS = 150

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
  pocket_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY (pocket_id, idx)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class FastEmbedder:
    """fastembed (ONNX) model; imported lazily because it is only needed when enabled."""

    def __init__(self, model: str):
        from fastembed import TextEmbedding

        self.name = model
        cache = config_dir() / "models"
        cache.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=model, cache_dir=str(cache))

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._model.embed(texts, batch_size=32)), dtype=np.float32)
        return _normalise(vecs)


def _normalise(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return (vecs / norms).astype(np.float32)


_embedders: dict[str, Embedder] = {}
_embedder_lock = threading.Lock()
_override: Embedder | None = None  # tests


def set_embedder(embedder: Embedder | None) -> None:
    global _override
    _override = embedder
    _matrix_cache.clear()


def get_embedder(settings: Settings) -> Embedder:
    if _override is not None:
        return _override
    with _embedder_lock:
        if settings.embed_model not in _embedders:
            _embedders[settings.embed_model] = FastEmbedder(settings.embed_model)
        return _embedders[settings.embed_model]


def model_downloaded(settings: Settings) -> bool:
    if _override is not None:
        return True
    folder = config_dir() / "models"
    tail = settings.embed_model.split("/")[-1].lower()
    return folder.exists() and any(tail in p.name.lower() for p in folder.iterdir())


# -- Chunking ---------------------------------------------------------------------


def chunk_text(title: str, summary: str, transcript: str) -> list[str]:
    chunks = [f"{title}\n{summary}".strip()[: CHUNK_CHARS * 2]]
    buf = ""
    for para in [p.strip() for p in transcript.split("\n") if p.strip()]:
        if len(buf) + len(para) + 1 > CHUNK_CHARS and buf:
            chunks.append(buf)
            buf = buf[-OVERLAP_CHARS:]
        buf = f"{buf}\n{para}" if buf else para
        while len(buf) > CHUNK_CHARS * 1.5:  # one very long paragraph
            chunks.append(buf[:CHUNK_CHARS])
            buf = buf[CHUNK_CHARS - OVERLAP_CHARS :]
    if buf.strip():
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


# -- Index ------------------------------------------------------------------------


def _prepare(index: Index, embedder: Embedder) -> None:
    index.conn.executescript(SCHEMA)
    row = index.conn.execute("SELECT value FROM kv WHERE key = 'model'").fetchone()
    if not row or row["value"] != embedder.name:  # model changed: start over
        with index.conn:
            index.conn.execute("DELETE FROM chunks")
            index.conn.execute("UPDATE recordings SET emb_mtime = 0")
            index.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('model', ?)", (embedder.name,))
        _matrix_cache.clear()


def update(settings: Settings, index: Index, embedder: Embedder | None = None, progress=None) -> int:
    """Embed recordings that are new or changed since the last run. Returns number of chunks written."""
    embedder = embedder or get_embedder(settings)
    _prepare(index, embedder)
    todo = index.conn.execute("SELECT pocket_id, path, mtime FROM recordings WHERE emb_mtime != mtime").fetchall()
    written = 0
    for i, r in enumerate(todo, 1):
        parsed = parse_markdown(Path(r["path"]))
        if not parsed:
            continue
        chunks = chunk_text(parsed.title, parsed.summary, parsed.transcript)
        vecs = embedder.embed(chunks)
        with index.conn:
            index.conn.execute("DELETE FROM chunks WHERE pocket_id = ?", (r["pocket_id"],))
            index.conn.executemany(
                "INSERT INTO chunks (pocket_id, idx, text, vec) VALUES (?,?,?,?)",
                [(r["pocket_id"], j, c, v.tobytes()) for j, (c, v) in enumerate(zip(chunks, vecs))],
            )
            index.conn.execute("UPDATE recordings SET emb_mtime = ? WHERE pocket_id = ?", (r["mtime"], r["pocket_id"]))
        written += len(chunks)
        if progress:
            progress(i, len(todo))
    # Drop chunks of recordings that no longer exist
    with index.conn:
        index.conn.execute("DELETE FROM chunks WHERE pocket_id NOT IN (SELECT pocket_id FROM recordings)")
    if written:
        _matrix_cache.clear()
    return written


_matrix_cache: dict[str, tuple] = {}


def _matrix(index: Index) -> tuple[list[str], list[str], np.ndarray]:
    key = str(index.db_path)
    count = index.conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM chunks").fetchone()
    stamp = (count[0], count[1])
    cached = _matrix_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1], cached[2], cached[3]
    rows = index.conn.execute("SELECT pocket_id, text, vec FROM chunks").fetchall()
    ids = [r["pocket_id"] for r in rows]
    texts = [r["text"] for r in rows]
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 1), np.float32)
    _matrix_cache[key] = (stamp, ids, texts, mat)
    return ids, texts, mat


def search(settings: Settings, index: Index, query: str, client: str | None = None, limit: int = 10, embedder: Embedder | None = None) -> list[dict]:
    embedder = embedder or get_embedder(settings)
    _prepare(index, embedder)
    ids, texts, mat = _matrix(index)
    if not ids:
        return []
    q = embedder.embed([query])[0]
    scores = mat @ q
    best: dict[str, tuple[float, str]] = {}
    for i in np.argsort(-scores)[: limit * 20]:
        pid = ids[i]
        if pid not in best:
            best[pid] = (float(scores[i]), texts[i])
    out = []
    for pid, (score, text) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        row = index.get(pid)
        if not row or (client and (row.client or "").lower() != client.lower()):
            continue
        snippet = " ".join(text.split())[:240]
        out.append({**row.as_dict(), "snippet": snippet + ("…" if len(text) > 240 else ""), "score": round(score, 3)})
        if len(out) >= limit:
            break
    return out


def hybrid_search(settings: Settings, index: Index, query: str, client: str | None = None, limit: int = 10) -> list[dict]:
    """Keyword + meaning, merged with reciprocal rank fusion. Falls back to keywords only."""
    keyword = index.search(query, client=client, limit=limit * 2) or index.search(query, client=client, limit=limit * 2, any_word=True)
    if not settings.semantic_search:
        return keyword[:limit]
    try:
        meaning = search(settings, index, query, client=client, limit=limit * 2)
    except Exception as exc:  # model missing / offline: keyword search still works
        log.warning("semantic search unavailable: %s", exc)
        return keyword[:limit]
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for rank, h in enumerate(keyword):
        scores[h["pocket_id"]] = scores.get(h["pocket_id"], 0) + 1 / (60 + rank)
        items[h["pocket_id"]] = h
    for rank, h in enumerate(meaning):
        scores[h["pocket_id"]] = scores.get(h["pocket_id"], 0) + 1 / (60 + rank)
        items.setdefault(h["pocket_id"], h)
    ranked = sorted(scores, key=lambda pid: -scores[pid])[:limit]
    return [items[pid] for pid in ranked]


def status(settings: Settings, index: Index) -> dict:
    index.conn.executescript(SCHEMA)
    total = index.conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    done = index.conn.execute("SELECT COUNT(*) FROM recordings WHERE emb_mtime = mtime").fetchone()[0]
    chunks = index.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {
        "enabled": settings.semantic_search,
        "model": settings.embed_model,
        "model_downloaded": model_downloaded(settings),
        "recordings": total,
        "indexed": done,
        "chunks": chunks,
    }
