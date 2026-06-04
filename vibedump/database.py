"""SQLite persistence for Vibe-Dump.

The MVP uses one serialized connection wrapper. HTTP handlers in later milestones
should enqueue writes instead of blocking directly on this class.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DumpRecord:
    id: int
    title: str
    status: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


class Database:
    """Small serialized SQLite wrapper with WAL, FK enforcement, and FTS5."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = Path(path) if path != ":memory:" else Path(":memory:")
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._configure()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def pragma(self, name: str) -> Any:
        with self._lock:
            return self._conn.execute(f"PRAGMA {name}").fetchone()[0]

    def initialize(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile(
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL DEFAULT 'Vibe Coder',
                    xp INTEGER NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    streak_days INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS achievements(
                    id INTEGER PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    unlocked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS memory_traits(
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS dumps(
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS turns(
                    id INTEGER PRIMARY KEY,
                    dump_id INTEGER NOT NULL REFERENCES dumps(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    audio_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS blueprints(
                    id INTEGER PRIMARY KEY,
                    dump_id INTEGER NOT NULL REFERENCES dumps(id) ON DELETE CASCADE,
                    markdown TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY,
                    dump_id INTEGER NOT NULL REFERENCES dumps(id) ON DELETE CASCADE,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    content,
                    title UNINDEXED,
                    dump_id UNINDEXED,
                    chunk_id UNINDEXED
                );

                CREATE TABLE IF NOT EXISTS provider_configs(
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(name, kind)
                );

                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, content, title, dump_id, chunk_id)
                    SELECT NEW.id, NEW.content, dumps.title, NEW.dump_id, NEW.id
                    FROM dumps WHERE dumps.id = NEW.dump_id;
                END;

                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                    DELETE FROM chunks_fts WHERE rowid = OLD.id;
                END;

                CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                    DELETE FROM chunks_fts WHERE rowid = OLD.id;
                    INSERT INTO chunks_fts(rowid, content, title, dump_id, chunk_id)
                    SELECT NEW.id, NEW.content, dumps.title, NEW.dump_id, NEW.id
                    FROM dumps WHERE dumps.id = NEW.dump_id;
                END;
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("INSERT OR IGNORE INTO profile(id) VALUES(1)")

    def create_dump(self, title: str, metadata: dict[str, Any] | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO dumps(title, metadata_json) VALUES(?, ?)",
                (title, json.dumps(metadata or {}, sort_keys=True)),
            )
            return int(cur.lastrowid)

    def get_dump(self, dump_id: int) -> DumpRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM dumps WHERE id = ?", (dump_id,)).fetchone()
        if row is None:
            return None
        return DumpRecord(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def add_turn(self, dump_id: int, role: str, text: str, audio_path: str | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO turns(dump_id, role, text, audio_path) VALUES(?, ?, ?, ?)",
                (dump_id, role, text, audio_path),
            )
            return int(cur.lastrowid)

    def add_blueprint(self, dump_id: int, markdown: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO blueprints(dump_id, markdown) VALUES(?, ?)",
                (dump_id, markdown),
            )
            return int(cur.lastrowid)

    def add_chunk(self, dump_id: int, source_type: str, source_id: int, chunk_index: int, content: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO chunks(dump_id, source_type, source_id, chunk_index, content)
                VALUES(?, ?, ?, ?, ?)
                """,
                (dump_id, source_type, source_id, chunk_index, content),
            )
            return int(cur.lastrowid)

    def search_chunks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT chunks_fts.content, chunks_fts.title, chunks_fts.dump_id, chunks_fts.chunk_id,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_dump(self, dump_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM dumps WHERE id = ?", (dump_id,))

    def count(self, table: str) -> int:
        allowed = {"dumps", "turns", "blueprints", "chunks", "chunks_fts"}
        if table not in allowed:
            raise ValueError(f"unsupported count table: {table}")
        with self._lock:
            return int(self._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
