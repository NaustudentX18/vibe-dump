"""SQLite persistence for Vibe-Dump.

The MVP uses one serialized connection wrapper. HTTP handlers in later milestones
should enqueue writes instead of blocking directly on this class.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Generator

SCHEMA_VERSION = 1
FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _lastrowid(cur: sqlite3.Cursor) -> int:
    """Return the just-inserted rowid, asserting the cursor produced one."""
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT did not produce a rowid")
    return rowid


@dataclass(frozen=True, slots=True)
class DumpRecord:
    id: int
    title: str
    status: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: int
    dump_id: int
    role: str
    text: str
    audio_path: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class BlueprintRecord:
    id: int
    dump_id: int
    markdown: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ProviderConfigRecord:
    id: int
    name: str
    kind: str
    enabled: bool
    config: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AchievementRecord:
    key: str
    title: str
    description: str
    unlocked_at: str | None


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    name: str
    xp: int
    level: int
    streak_days: int


DEFAULT_ACHIEVEMENTS: list[tuple[str, str, str]] = [
    ("first_dump", "First Dump", "Create your first vibe dump."),
    ("first_blueprint", "Spec Goblin", "Generate your first blueprint."),
    ("streak_3", "3-Day Streak", "Use Vibe-Dump three days running."),
    ("level_2", "Apprentice", "Reach level 2."),
    ("xp_100", "Centurion", "Earn 100 XP."),
]


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
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
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
            conn.executemany(
                "INSERT OR IGNORE INTO achievements(key, title, description) VALUES(?, ?, ?)",
                DEFAULT_ACHIEVEMENTS,
            )

    def create_dump(self, title: str, metadata: dict[str, Any] | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO dumps(title, metadata_json) VALUES(?, ?)",
                (title, json.dumps(metadata or {}, sort_keys=True)),
            )
            return _lastrowid(cur)

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

    def list_dumps(self, limit: int = 50, offset: int = 0) -> list[DumpRecord]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM dumps ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            DumpRecord(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def update_dump_status(self, dump_id: int, status: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE dumps SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, dump_id),
            )
            return cur.rowcount > 0

    def list_turns(self, dump_id: int) -> list[TurnRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM turns WHERE dump_id = ? ORDER BY id ASC",
                (dump_id,),
            ).fetchall()
        return [
            TurnRecord(
                id=row["id"],
                dump_id=row["dump_id"],
                role=row["role"],
                text=row["text"],
                audio_path=row["audio_path"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_latest_blueprint(self, dump_id: int) -> BlueprintRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM blueprints WHERE dump_id = ? ORDER BY id DESC LIMIT 1",
                (dump_id,),
            ).fetchone()
        if row is None:
            return None
        return BlueprintRecord(
            id=row["id"],
            dump_id=row["dump_id"],
            markdown=row["markdown"],
            created_at=row["created_at"],
        )

    def upsert_provider_config(
        self,
        name: str,
        kind: str,
        enabled: bool,
        config: dict[str, Any],
    ) -> int:
        payload = json.dumps(config, sort_keys=True)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO provider_configs(name, kind, enabled, config_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(name, kind) DO UPDATE SET
                    enabled = excluded.enabled,
                    config_json = excluded.config_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (name, kind, 1 if enabled else 0, payload),
            )
            row = conn.execute(
                "SELECT id FROM provider_configs WHERE name = ? AND kind = ?",
                (name, kind),
            ).fetchone()
            return int(row["id"])

    def list_provider_configs(self) -> list[ProviderConfigRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM provider_configs ORDER BY kind, name"
            ).fetchall()
        return [
            ProviderConfigRecord(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                enabled=bool(row["enabled"]),
                config=json.loads(row["config_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def add_turn(self, dump_id: int, role: str, text: str, audio_path: str | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO turns(dump_id, role, text, audio_path) VALUES(?, ?, ?, ?)",
                (dump_id, role, text, audio_path),
            )
            return _lastrowid(cur)

    def add_blueprint(self, dump_id: int, markdown: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO blueprints(dump_id, markdown) VALUES(?, ?)",
                (dump_id, markdown),
            )
            return _lastrowid(cur)

    def add_chunk(self, dump_id: int, source_type: str, source_id: int, chunk_index: int, content: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO chunks(dump_id, source_type, source_id, chunk_index, content)
                VALUES(?, ?, ?, ?, ?)
                """,
                (dump_id, source_type, source_id, chunk_index, content),
            )
            return _lastrowid(cur)

    def search_chunks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        fts_query = " ".join(FTS_TOKEN_RE.findall(query))
        if not fts_query:
            return []
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
                (fts_query, limit),
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

    def seed_achievements(self, rows: list[tuple[str, str, str]]) -> None:
        """Bulk-insert achievement definitions keyed by ``key``.

        Uses ``INSERT OR IGNORE`` so re-seeding is a no-op for existing rows.
        """
        with self.transaction() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO achievements(key, title, description) VALUES(?, ?, ?)",
                rows,
            )

    def unlock_achievement(self, key: str) -> bool:
        """Mark an achievement as unlocked the first time.

        Returns True if this call newly unlocked it, False if it was already
        unlocked or the key is unknown. Never raises on unknown keys.
        """
        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE achievements
                SET unlocked_at = CURRENT_TIMESTAMP
                WHERE key = ? AND unlocked_at IS NULL
                """,
                (key,),
            )
            return cur.rowcount > 0

    def list_achievements(self) -> list[AchievementRecord]:
        """Return all seeded achievements (locked + unlocked) in stable order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, title, description, unlocked_at FROM achievements ORDER BY id ASC"
            ).fetchall()
        return [
            AchievementRecord(
                key=row["key"],
                title=row["title"],
                description=row["description"],
                unlocked_at=row["unlocked_at"],
            )
            for row in rows
        ]

    def get_profile(self) -> ProfileRecord:
        """Return the singleton profile row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT name, xp, level, streak_days FROM profile WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("profile row missing — call db.initialize() first")
        return ProfileRecord(
            name=row["name"],
            xp=int(row["xp"]),
            level=int(row["level"]),
            streak_days=int(row["streak_days"]),
        )

    def update_profile_name(self, name: str) -> bool:
        """Update the profile name and bump ``updated_at``."""
        with self.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE profile
                SET name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (name,),
            )
            return cur.rowcount > 0

    def grant_xp(self, amount: int) -> ProfileRecord:
        """Add ``amount`` to profile.xp and recompute level.

        Level rule: ``level = 1 + (xp // 100)``. Returns the updated record.
        """
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE profile
                SET xp = xp + ?,
                    level = 1 + ((xp + ?) / 100),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (amount, amount),
            )
            row = conn.execute(
                "SELECT name, xp, level, streak_days FROM profile WHERE id = 1"
            ).fetchone()
        return ProfileRecord(
            name=row["name"],
            xp=int(row["xp"]),
            level=int(row["level"]),
            streak_days=int(row["streak_days"]),
        )

    def increment_streak(self) -> int:
        """Increment ``streak_days`` by 1 and return the new value."""
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE profile
                SET streak_days = streak_days + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """
            )
            row = conn.execute(
                "SELECT streak_days FROM profile WHERE id = 1"
            ).fetchone()
        return int(row["streak_days"])
