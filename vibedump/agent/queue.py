"""SQLite-backed job queue for the openlaude agent.

Public surface: ``JobRecord`` and ``JobQueue``. Uses autocommit + WAL for
safe concurrent claimers; ``claim`` is atomic via ``UPDATE ... RETURNING``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 0, prompt TEXT NOT NULL,
  result TEXT, error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  created_at TEXT NOT NULL, claimed_at TEXT, completed_at TEXT, metadata_json TEXT);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_status_priority ON agent_jobs(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_kind ON agent_jobs(kind);
"""
_FIELDS = "id, kind, status, priority, prompt, result, error, attempts, max_attempts, created_at, claimed_at, completed_at, metadata_json"


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    kind: str
    status: str
    priority: int
    prompt: str
    result: str | None
    error: str | None
    attempts: int
    max_attempts: int
    created_at: str
    claimed_at: str | None
    completed_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _row(r: sqlite3.Row | None) -> JobRecord | None:
    if r is None:
        return None
    d = dict(r)
    raw = d.pop("metadata_json")
    return JobRecord(metadata=json.loads(raw) if raw else {}, **d)


class JobQueue:
    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(Path(db_path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        # Serialise multi-thread access. SQLite3 python module + shared
        # connection can race on the GIL release boundaries even with
        # check_same_thread=False; the SQL itself is atomic but the
        # Python-side state isn't safe under N>1 claimers.
        self._lock = threading.RLock()

    def enqueue(self, kind: str, prompt: str, *, priority: int = 0, max_attempts: int = 3,
                metadata: dict[str, Any] | None = None) -> JobRecord:
        with self._lock:
            new_id, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO agent_jobs (id,kind,priority,prompt,max_attempts,created_at,metadata_json) VALUES (?,?,?,?,?,?,?)",
                (new_id, kind, priority, prompt, max_attempts, now, json.dumps(metadata or {})),
            )
            return self.get(new_id)  # type: ignore[return-value]

    def claim(self) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "UPDATE agent_jobs SET status='claimed', claimed_at=?, attempts=attempts+1"
                " WHERE id = (SELECT id FROM agent_jobs WHERE status='pending'"
                " ORDER BY priority DESC, created_at LIMIT 1) RETURNING " + _FIELDS,
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone()
            return _row(row)

    def complete(self, job_id: str, result: str) -> None:
        self._conn.execute(
            "UPDATE agent_jobs SET status='complete', completed_at=?, result=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), result, job_id),
        )

    def fail(self, job_id: str, error: str, retry: bool = True) -> None:
        row = self._conn.execute("SELECT attempts, max_attempts FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return
        if retry and row["attempts"] < row["max_attempts"]:
            self._conn.execute("UPDATE agent_jobs SET status='pending', error=? WHERE id=?", (error, job_id))
        else:
            self._conn.execute(
                "UPDATE agent_jobs SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, datetime.now(timezone.utc).isoformat(), job_id),
            )

    def cancel(self, job_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE agent_jobs SET status='cancelled', completed_at=? WHERE id=? AND status IN ('pending','claimed')",
            (datetime.now(timezone.utc).isoformat(), job_id),
        )
        return cur.rowcount > 0

    def get(self, job_id: str) -> JobRecord | None:
        return _row(self._conn.execute(f"SELECT {_FIELDS} FROM agent_jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, *, status: str | None = None, kind: str | None = None, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            sql, args = f"SELECT {_FIELDS} FROM agent_jobs WHERE 1=1", []
            if status is not None:
                sql += " AND status=?"; args.append(status)
            if kind is not None:
                sql += " AND kind=?"; args.append(kind)
            sql += " ORDER BY priority DESC, created_at LIMIT ?"; args.append(limit)
            out: list[JobRecord] = []
            for r in self._conn.execute(sql, args).fetchall():
                rec = _row(r)
                if rec is not None:
                    out.append(rec)
            return out

    def reap_stale(self, older_than_seconds: int = 300) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        return self._conn.execute(
            "UPDATE agent_jobs SET status='pending', claimed_at=NULL"
            " WHERE status='claimed' AND claimed_at IS NOT NULL AND claimed_at < ?",
            (cutoff,),
        ).rowcount
