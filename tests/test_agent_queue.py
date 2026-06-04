"""Tests for the SQLite-backed agent job queue."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vibedump.agent.queue import JobQueue, JobRecord


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    q = JobQueue(tmp_path / "queue.sqlite3")
    yield q
    q._conn.close()


def test_enqueue_returns_record_with_id(queue: JobQueue) -> None:
    rec = queue.enqueue("echo", "hello")
    assert isinstance(rec, JobRecord)
    assert rec.id and len(rec.id) == 32
    uuid.UUID(hex=rec.id)  # parses as uuid hex
    assert rec.kind == "echo"
    assert rec.prompt == "hello"
    assert rec.status == "pending"
    assert rec.attempts == 0


def test_enqueue_assigns_utc_timestamp(queue: JobQueue) -> None:
    rec = queue.enqueue("echo", "hello")
    parsed = datetime.fromisoformat(rec.created_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def test_claim_returns_pending_in_priority_order(queue: JobQueue) -> None:
    low = queue.enqueue("kind", "low", priority=1)
    high = queue.enqueue("kind", "high", priority=10)
    first = queue.claim()
    second = queue.claim()
    assert first is not None and first.id == high.id
    assert second is not None and second.id == low.id
    assert first.status == "claimed"
    assert first.attempts == 1
    assert first.claimed_at is not None


def test_claim_returns_none_when_empty(queue: JobQueue) -> None:
    assert queue.claim() is None


def test_claim_is_atomic_under_concurrency(queue: JobQueue) -> None:
    queue.enqueue("kind", "only-one", priority=0)
    barrier = threading.Barrier(8)
    results: list[JobRecord | None] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        claimed = queue.claim()
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}"
    assert results.count(None) == 7


def test_complete_sets_status_and_timestamp(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "do work")
    claimed = queue.claim()
    assert claimed is not None
    queue.complete(claimed.id, "all done")
    fetched = queue.get(claimed.id)
    assert fetched is not None
    assert fetched.status == "complete"
    assert fetched.completed_at is not None
    assert fetched.result == "all done"


def test_fail_with_retry_resets_to_pending(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "flaky")
    claimed = queue.claim()
    assert claimed is not None
    queue.fail(claimed.id, "boom", retry=True)
    fetched = queue.get(claimed.id)
    assert fetched is not None
    assert fetched.status == "pending"
    assert fetched.error == "boom"
    assert fetched.attempts == 1  # attempts persists; not reset on retry


def test_fail_without_retry_marks_failed(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "doomed", max_attempts=1)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.attempts == 1
    queue.fail(claimed.id, "boom", retry=False)
    fetched = queue.get(claimed.id)
    assert fetched is not None
    assert fetched.status == "failed"
    assert fetched.completed_at is not None
    assert fetched.error == "boom"


def test_cancel_pending_succeeds(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "cancel me")
    assert queue.cancel(rec.id) is True
    fetched = queue.get(rec.id)
    assert fetched is not None
    assert fetched.status == "cancelled"
    assert fetched.completed_at is not None


def test_cancel_completed_returns_false(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "do work")
    claimed = queue.claim()
    assert claimed is not None
    queue.complete(claimed.id, "ok")
    assert queue.cancel(claimed.id) is False
    fetched = queue.get(claimed.id)
    assert fetched is not None
    assert fetched.status == "complete"


def test_list_jobs_filters_by_status(queue: JobQueue) -> None:
    a = queue.enqueue("alpha", "a")
    b = queue.enqueue("alpha", "b")
    c = queue.enqueue("beta", "c")
    claimed = queue.claim()
    assert claimed is not None and claimed.id == a.id  # oldest pending
    pending = queue.list_jobs(status="pending")
    assert {j.id for j in pending} == {b.id, c.id}
    by_kind = queue.list_jobs(kind="alpha")
    assert {j.id for j in by_kind} == {a.id, b.id}
    assert all(isinstance(j, JobRecord) for j in pending)


def test_reap_stale_resets_claimed_to_pending(queue: JobQueue) -> None:
    rec = queue.enqueue("kind", "stuck")
    claimed = queue.claim()
    assert claimed is not None
    # backdate the claimed_at to simulate an old in-flight job
    queue._conn.execute(
        "UPDATE agent_jobs SET claimed_at=? WHERE id=?",
        ("2000-01-01T00:00:00+00:00", claimed.id),
    )
    reaped = queue.reap_stale(older_than_seconds=60)
    assert reaped == 1
    fetched = queue.get(claimed.id)
    assert fetched is not None
    assert fetched.status == "pending"
    assert fetched.claimed_at is None


def test_wal_mode_active(queue: JobQueue) -> None:
    mode = queue._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
