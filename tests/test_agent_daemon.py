"""Tests for the M9 ``OpenClaudeDaemon`` runner.

These tests use a real ``JobQueue`` against a tmp SQLite file plus a
``FakeOpenClaude`` that mimics the ``run(user_message, on_step=None)``
contract. The daemon is exercised in ``run_once`` mode so each test
gets a deterministic single iteration.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from vibedump.agent.daemon import OpenClaudeDaemon
from vibedump.agent.queue import JobQueue


class FakeOpenClaude:
    """Stand-in for the parallel-agent ``OpenClaude`` runtime.

    Configurable via ``raise_on_run`` / ``result_value`` / ``step_delay``.
    The ``on_step`` callback (if provided) is invoked at least once
    with a small dict so the daemon's plumbing can be observed.
    """

    def __init__(
        self,
        result_value: str = "ok",
        raise_on_run: Exception | None = None,
        step_delay: float = 0.0,
    ) -> None:
        self.result_value = result_value
        self.raise_on_run = raise_on_run
        self.step_delay = step_delay
        self.calls: list[str] = []
        self._on_step_seen: threading.Event = threading.Event()

    def run(self, user_message: str, on_step: Any = None) -> str:
        self.calls.append(user_message)
        if on_step is not None:
            try:
                on_step({"kind": "step", "prompt": user_message})
            except Exception:
                pass
            self._on_step_seen.set()
        if self.step_delay:
            time.sleep(self.step_delay)
        if self.raise_on_run is not None:
            raise self.raise_on_run
        return self.result_value


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / "daemon.sqlite3")


@pytest.fixture
def fake_agent() -> FakeOpenClaude:
    return FakeOpenClaude(result_value="the answer")


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_daemon_rejects_none_queue() -> None:
    """The constructor raises ValueError on missing required args."""
    with pytest.raises(ValueError):
        OpenClaudeDaemon(queue=None, agent=FakeOpenClaude())  # type: ignore[arg-type]


def test_daemon_rejects_none_agent() -> None:
    with pytest.raises(ValueError):
        OpenClaudeDaemon(queue=JobQueue(":memory:"), agent=None)  # type: ignore[arg-type]


def test_daemon_rejects_negative_poll() -> None:
    with pytest.raises(ValueError):
        OpenClaudeDaemon(
            queue=JobQueue(":memory:"),
            agent=FakeOpenClaude(),
            poll_interval=-0.1,
        )


# ---------------------------------------------------------------------------
# run_once: happy path
# ---------------------------------------------------------------------------


def test_agent_daemon_run_once_processes_claimable_job(
    queue: JobQueue, fake_agent: FakeOpenClaude
) -> None:
    """A pending job is claimed, the agent is called, and the row is updated."""
    record = queue.enqueue("oneshot", "do the thing", priority=0)
    daemon = OpenClaudeDaemon(queue=queue, agent=fake_agent, poll_interval=0.01)
    processed = daemon.run_once()
    assert processed is not None
    assert processed.id == record.id
    assert fake_agent.calls == ["do the thing"]
    after = queue.get(record.id)
    assert after is not None
    assert after.status == "complete"
    assert after.result == "the answer"


def test_agent_daemon_run_once_returns_none_when_empty(
    queue: JobQueue, fake_agent: FakeOpenClaude
) -> None:
    """An empty queue makes run_once return None without calling the agent."""
    daemon = OpenClaudeDaemon(queue=queue, agent=fake_agent, poll_interval=0.0)
    assert daemon.run_once() is None
    assert fake_agent.calls == []


# ---------------------------------------------------------------------------
# run_once: complete path
# ---------------------------------------------------------------------------


def test_agent_daemon_complete_calls_queue_complete(
    queue: JobQueue,
) -> None:
    """A successful agent.run() flows into queue.complete with the result text."""
    agent = FakeOpenClaude(result_value="42")
    record = queue.enqueue("oneshot", "compute")
    daemon = OpenClaudeDaemon(queue=queue, agent=agent, poll_interval=0.01)
    daemon.run_once()
    after = queue.get(record.id)
    assert after is not None
    assert after.status == "complete"
    assert after.result == "42"
    assert after.completed_at is not None


# ---------------------------------------------------------------------------
# run_once: failure path
# ---------------------------------------------------------------------------


def test_agent_daemon_fail_calls_queue_fail_with_retry(
    queue: JobQueue,
) -> None:
    """A raising agent results in queue.fail(retry=True), so the job returns
    to ``pending`` and the error is stashed on the row."""
    boom = RuntimeError("kaboom")
    agent = FakeOpenClaude(raise_on_run=boom)
    record = queue.enqueue("oneshot", "explode", max_attempts=3)
    daemon = OpenClaudeDaemon(queue=queue, agent=agent, poll_interval=0.01)
    daemon.run_once()
    after = queue.get(record.id)
    assert after is not None
    # fail(retry=True) puts the job back in pending because attempts
    # (now 1) is still < max_attempts (3).
    assert after.status == "pending"
    assert after.error == "kaboom"
    assert after.attempts == 1


def test_agent_daemon_fail_marks_failed_when_max_attempts_reached(
    queue: JobQueue,
) -> None:
    """When attempts already equal max_attempts, fail() flips to ``failed``."""
    boom = RuntimeError("no more tries")
    agent = FakeOpenClaude(raise_on_run=boom)
    # max_attempts=1 + the daemon's own claim() bump => attempts=1, which
    # makes the subsequent fail(retry=True) flip to ``failed`` instead of
    # re-queueing as ``pending``.
    record = queue.enqueue("oneshot", "explode", max_attempts=1)
    daemon = OpenClaudeDaemon(queue=queue, agent=agent, poll_interval=0.01)
    daemon.run_once()
    after = queue.get(record.id)
    assert after is not None
    assert after.attempts == 1
    assert after.status == "failed"
    assert after.error == "no more tries"


# ---------------------------------------------------------------------------
# run_once: crash isolation
# ---------------------------------------------------------------------------


def test_agent_daemon_crash_does_not_kill_loop(
    queue: JobQueue,
) -> None:
    """If the agent raises, the daemon's NEXT run_once still works.

    We enqueue two jobs: a ``boom`` that crashes the first iteration
    (and is then marked ``failed`` because max_attempts=1), and a
    follow-up ``survive`` that the second iteration must run
    successfully.
    """
    queue.enqueue("oneshot", "boom", max_attempts=1)
    queue.enqueue("oneshot", "survive", priority=0)
    # First agent: raises on the first prompt, returns ok on the second.
    state = {"count": 0}

    class TwoShot:
        def run(self, user_message: str, on_step: Any = None) -> str:
            state["count"] += 1
            if state["count"] == 1:
                raise RuntimeError("deliberate crash")
            return f"got: {user_message}"

    daemon = OpenClaudeDaemon(queue=queue, agent=TwoShot(), poll_interval=0.0)
    first = daemon.run_once()
    second = daemon.run_once()
    third = daemon.run_once()  # queue is empty -> None
    assert first is not None
    assert second is not None
    assert third is None
    # The boom job is permanently failed, the survive job is complete.
    boom = queue.get(first.id)
    survive = queue.get(second.id)
    assert boom is not None and survive is not None
    assert boom.status == "failed" and boom.error == "deliberate crash"
    assert survive.status == "complete" and survive.result == "got: survive"


# ---------------------------------------------------------------------------
# cancel: best-effort
# ---------------------------------------------------------------------------


def test_agent_daemon_cancel_flags_in_flight(
    queue: JobQueue, fake_agent: FakeOpenClaude
) -> None:
    """Calling cancel() while the daemon has the job flagged returns True
    and sets the cancel_event so a long-running agent can observe it."""
    state = {"in_flight": False}

    class SlowAgent:
        def run(self, user_message: str, on_step: Any = None) -> str:
            state["in_flight"] = True
            # Spin briefly so the test thread can flip cancel_event.
            for _ in range(20):
                if on_step is not None:
                    try:
                        on_step({"kind": "tick"})
                    except Exception:
                        pass
                time.sleep(0.005)
            return "slow"

    queue.enqueue("oneshot", "go")
    daemon = OpenClaudeDaemon(queue=queue, agent=SlowAgent(), poll_interval=0.0)
    # Start the run_once in a background thread.
    t = threading.Thread(target=daemon.run_once)
    t.start()
    # Wait until the agent is actually in flight, then cancel.
    deadline = time.monotonic() + 1.0
    while not state["in_flight"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert state["in_flight"], "agent never started"
    # ``_current_job_id`` may not be visible yet; cancel is best-effort
    # regardless and returns True because the queue row is also cancelled.
    job_id = queue.list_jobs(limit=1)[0].id
    cancelled = daemon.cancel(job_id)
    assert cancelled is True
    assert daemon.cancel_event.is_set()
    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# run_forever: graceful stop
# ---------------------------------------------------------------------------


def test_agent_daemon_run_forever_stops_on_event() -> None:
    """run_forever exits when ``_stop`` is set, even if the queue is empty."""
    queue = JobQueue(":memory:")
    agent = FakeOpenClaude()
    daemon = OpenClaudeDaemon(queue=queue, agent=agent, poll_interval=0.0)

    def stopper() -> None:
        # Give the loop a moment to enter the empty-poll sleep, then stop.
        time.sleep(0.05)
        daemon._stop.set()  # type: ignore[attr-defined]

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    started = time.monotonic()
    daemon.run_forever()
    elapsed = time.monotonic() - started
    t.join(timeout=1.0)
    # The loop should exit promptly after _stop fires.
    assert elapsed < 2.0
