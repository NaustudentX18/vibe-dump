"""MemoryStore: FTS5-backed recall with optional embedding stubs.

Wraps :class:`~vibedump.database.Database` and
:class:`~vibedump.ragmemory.RagMemory` to provide a domain-level
``recall`` / ``learn`` / ``forget`` API used by the agent pipeline.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..database import Database
from ..ragmemory import RagMemory
from .embeddings import Embedder, FakeEmbedder
from .schema import RecallHit

_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class MemoryStore:
    """High-level memory API wrapping the existing Database/RagMemory patterns.

    Parameters
    ----------
    db:
        The :class:`~vibedump.database.Database` instance to read from and
        write to.  The caller is responsible for calling ``db.initialize()``
        before constructing a :class:`MemoryStore`.
    embedder:
        Optional :class:`~vibedump.memory.embeddings.Embedder`.  Defaults to
        :class:`~vibedump.memory.embeddings.FakeEmbedder` so tests run without
        ML dependencies.
    chunk_size / overlap:
        Forwarded to the underlying :class:`~vibedump.ragmemory.RagMemory`.
    """

    def __init__(
        self,
        db: Database,
        embedder: Embedder | None = None,
        *,
        chunk_size: int = 600,
        overlap: int = 80,
    ) -> None:
        self._db = db
        self._rag = RagMemory(db, chunk_size=chunk_size, overlap=overlap)
        self._embedder: Embedder = embedder or FakeEmbedder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def learn(
        self,
        dump_id: int,
        source_type: str,
        source_id: int,
        text: str,
    ) -> list[int]:
        """Chunk and index ``text`` for later recall.

        Delegates to :class:`~vibedump.ragmemory.RagMemory.remember` and
        returns the list of inserted chunk row-ids.
        """
        return self._rag.remember(dump_id, source_type, source_id, text)

    def recall(self, query: str, limit: int = 10) -> list[RecallHit]:
        """Full-text search across all indexed chunks.

        Returns a list of :class:`~vibedump.memory.schema.RecallHit` sorted
        by BM25 score (best match first).  An empty or all-punctuation query
        returns an empty list without hitting the DB.
        """
        raw_rows: list[dict[str, Any]] = self._db.search_chunks(query, limit=limit)
        hits: list[RecallHit] = []
        for row in raw_rows:
            hits.append(
                RecallHit(
                    content=row.get("content", ""),
                    title=row.get("title", ""),
                    dump_id=int(row.get("dump_id") or 0),
                    chunk_id=int(row.get("chunk_id") or 0),
                    score=float(row.get("score") or 0.0),
                )
            )
        return hits

    def forget(self, dump_id: int) -> None:
        """Remove all memory chunks for a dump (cascades via the DB trigger)."""
        self._db.delete_dump(dump_id)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts`` using the configured embedder."""
        return self._embedder.embed(texts)
