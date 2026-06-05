"""LessonStore: lightweight FTS-backed lesson persistence.

Lessons are short free-form strings extracted by the
:class:`~vibedump.agent.evolution.EvolutionHook` after each pipeline run.
They live in their own table so they are never co-mingled with RAG chunks.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .schema import LessonRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..database import Database


_DDL = """
CREATE TABLE IF NOT EXISTS lessons(
    id          INTEGER PRIMARY KEY,
    text        TEXT    NOT NULL,
    tags_json   TEXT    NOT NULL DEFAULT '[]',
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
    text,
    lesson_id UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS lessons_ai AFTER INSERT ON lessons BEGIN
    INSERT INTO lessons_fts(rowid, text, lesson_id) VALUES(NEW.id, NEW.text, NEW.id);
END;

CREATE TRIGGER IF NOT EXISTS lessons_ad AFTER DELETE ON lessons BEGIN
    DELETE FROM lessons_fts WHERE rowid = OLD.id;
END;
"""


class LessonStore:
    """Persist and search agent-extracted lessons.

    The store creates its own table schema on first use, piggybacking on the
    existing :class:`~vibedump.database.Database` connection so no additional
    SQLite file is needed.
    """

    def __init__(self, db: "Database") -> None:
        self._db = db
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._db.transaction() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, text: str, tags: list[str] | None = None) -> int:
        """Persist a lesson and return its row-id."""
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO lessons(text, tags_json) VALUES(?, ?)",
                (text, json.dumps(tags or [])),
            )
            rowid = cur.lastrowid
            if rowid is None:
                raise RuntimeError("INSERT INTO lessons did not return a rowid")
            return rowid

    def delete(self, lesson_id: int) -> bool:
        """Remove a lesson by id.  Returns True if a row was deleted."""
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[LessonRecord]:
        """FTS5 search across lesson text.  Returns an empty list for blank queries."""
        import re

        tokens = " ".join(re.findall(r"\w+", query, re.UNICODE))
        if not tokens:
            return []
        with self._db._lock:  # noqa: SLF001 - sharing connection intentionally
            rows = self._db._conn.execute(  # noqa: SLF001
                """
                SELECT l.id, l.text, l.tags_json, l.created_at
                FROM lessons_fts
                JOIN lessons l ON l.id = lessons_fts.lesson_id
                WHERE lessons_fts MATCH ?
                ORDER BY bm25(lessons_fts)
                LIMIT ?
                """,
                (tokens, limit),
            ).fetchall()
        return [
            LessonRecord(
                id=row["id"],
                text=row["text"],
                tags=json.loads(row["tags_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_all(self, limit: int = 100) -> list[LessonRecord]:
        """Return lessons in insertion order."""
        with self._db._lock:  # noqa: SLF001
            rows = self._db._conn.execute(  # noqa: SLF001
                "SELECT id, text, tags_json, created_at FROM lessons ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            LessonRecord(
                id=row["id"],
                text=row["text"],
                tags=json.loads(row["tags_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
