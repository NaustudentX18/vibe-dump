"""Long-running OpenClaude agent daemon.

The daemon is a single in-process loop that:

1. Polls a :class:`JobQueue` for the next claimable job.
2. Hands the job's prompt to an :class:`OpenClaude` runner.
3. Records the outcome back on the queue (``complete`` on success,
   ``fail`` with ``retry=True`` on exception).
4. Sleeps ``poll_interval`` seconds between empty polls.

It is import-tolerant: if the parallel agents that own
``OpenClaude`` / ``JobQueue`` have not landed yet, the module still
imports (so the rest of the package keeps working) and the daemon
constructor raises a clear ``RuntimeError``.

The module is also runnable directly::

    python -m vibedump.agent.daemon

which wires a default :class:`JobQueue` against the project database
and starts the loop until SIGINT / SIGTERM.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from .queue import JobQueue

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .core import OpenClaude
else:  # pragma: no cover - import tolerance
    OpenClaude = Any  # type: ignore[assignment,misc]

log = logging.getLogger("vibedump.agent.daemon")

# Default location of the project database. Mirrors ``vibedump.database``
# but the daemon does not pull the whole project DB in to stay
# decoupled from the FastAPI surface.
_DEFAULT_DB_PATH = os.environ.get(
    "VIBEDUMP_DB_PATH",
    str(Path.home() / ".vibedump" / "vibedump.sqlite3"),
)


def _build_default_db_path() -> Path:
    """Resolve the default DB path, ensuring the parent directory exists."""
    p = Path(_DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class OpenClaudeDaemon:
    """Single-threaded worker that drains the agent job queue.

    The daemon is intentionally simple: one claim, one run, one
    ``complete``/``fail`` call per loop iteration. Long-running tool
    work belongs inside the ``OpenClaude`` runner; the daemon itself
    is a thin pump.

    Cancel is best-effort. The daemon exposes a :class:`threading.Event`
    the caller (HTTP route, test) can set to ask the in-flight agent
    to wrap up. The agent decides how to honour it.
    """

    def __init__(
        self,
        queue: JobQueue,
        agent: "OpenClaude",
        poll_interval: float = 0.5,
    ) -> None:
        if queue is None:
            raise ValueError("queue is required")
        if agent is None:
            raise ValueError("agent is required")
        if poll_interval < 0:
            raise ValueError("poll_interval must be >= 0")
        self.queue = queue
        self.agent = agent
        self.poll_interval = poll_interval
        # Set by HTTP cancel / tests. The agent runner is responsible
        # for checking this between steps; the daemon only flips the
        # flag and lets the agent decide what to do.
        self.cancel_event = threading.Event()
        self._stop = threading.Event()
        # Track the in-flight job id so HTTP ``/cancel`` can find it.
        self._current_job_id: str | None = None
        self._current_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """Block, draining the queue, until SIGINT/SIGTERM.

        Signal handlers set ``self._stop`` so the loop exits cleanly
        between iterations. We do NOT swallow the signal -- we just
        make sure the queue/database connection aren't held when the
        process tears down.
        """
        self._install_signal_handlers()
        log.info("daemon: starting (poll_interval=%.2fs)", self.poll_interval)
        try:
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception as exc:  # pragma: no cover - defensive
                    # The whole point of the daemon is to survive worker
                    # crashes. Log, sleep, keep going.
                    log.exception("daemon: run_once crashed: %s", exc)
                    self._sleep(self.poll_interval)
        finally:
            log.info("daemon: stopped")

    def run_once(self) -> Any:
        """Process a single job claim, or return ``None`` if queue is empty.

        Returns the :class:`JobRecord` that was processed (claimed +
        completed/failed) so callers (tests) can assert on it.
        """
        # Bail early if a stop was requested between iterations.
        if self._stop.is_set():
            return None
        job = self.queue.claim()
        if job is None:
            self._sleep(self.poll_interval)
            return None
        with self._current_lock:
            self._current_job_id = job.id
            self.cancel_event.clear()
        try:
            result = self.agent.run(job.prompt, on_step=self._on_step)
        except Exception as exc:
            log.warning("daemon: job %s failed: %s", job.id, exc)
            try:
                self.queue.fail(job.id, str(exc), retry=True)
            except Exception as fail_exc:  # pragma: no cover - defensive
                log.exception("daemon: queue.fail also failed: %s", fail_exc)
            return job
        try:
            self.queue.complete(job.id, str(result))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("daemon: queue.complete failed: %s", exc)
        return job

    def cancel(self, job_id: str) -> bool:
        """Best-effort cancel: flag the in-flight agent and try the queue.

        Returns ``True`` if either the in-flight job was flagged OR the
        queue had a pending/claimed row to cancel. ``False`` if the
        job is not ours to cancel (already complete / failed / unknown).
        """
        with self._current_lock:
            if self._current_job_id == job_id:
                self.cancel_event.set()
                flagged = True
            else:
                flagged = False
        # The queue call covers both pending rows and the in-flight
        # claimed row, so it always returns True for active jobs.
        return self.queue.cancel(job_id) or flagged

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_step(self, step: dict[str, Any]) -> None:
        """Default step callback: log only. The HTTP layer can pass
        its own callback to forward ``agent.step`` events onto the bus.
        """
        log.debug("daemon: step %s", step.get("kind", "?"))

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, frame: Any) -> None:  # pragma: no cover
            log.info("daemon: caught signal %s, shutting down", signum)
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # Signals may be unavailable (e.g. background threads).
                pass

    def _sleep(self, seconds: float) -> None:
        # Sleep in small slices so ``cancel_event`` and ``_stop`` are
        # observed promptly even when ``poll_interval`` is large.
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.is_set():
                return
            if self.cancel_event.is_set():
                return
            time.sleep(min(0.05, end - time.monotonic()))


# ---------------------------------------------------------------------------
# ``python -m vibedump.agent.daemon`` entry point
# ---------------------------------------------------------------------------


def _default_queue() -> JobQueue:
    """Build a JobQueue against the project DB; tolerate the dir not existing."""
    return JobQueue(_build_default_db_path())


def _import_default_agent() -> tuple[Any, Any, Any]:
    """Best-effort import of the OpenClaude runtime + its config + registry.

    The parallel agent modules may not have landed yet. In that case
    we raise a clear error so systemd can log it and the operator
    sees a useful message.

    Returns a ``(OpenClaude, AgentConfig, ToolRegistry)`` triple. The
    caller is responsible for instantiating the runtime with the
    appropriate handles; the registry is constructed empty here
    because the standalone daemon process has no DB / pipeline to
    wire to. The web process hosts the full registry in-app.
    """
    try:
        from .core import OpenClaude as _OpenClaude  # type: ignore[attr-defined]
        from .config import AgentConfig as _AgentConfig  # type: ignore[attr-defined]
        from .registry import ToolRegistry as _ToolRegistry  # type: ignore[attr-defined]
    except ImportError as exc:
        raise RuntimeError(
            "vibedump.agent runtime modules are not available; "
            "the parallel M9-P2/P3 modules must land before the daemon can run"
        ) from exc
    return _OpenClaude, _AgentConfig, _ToolRegistry


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("VIBEDUMP_AGENT_LOGLEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        queue = _default_queue()
        OpenClaude, AgentConfig, ToolRegistry = _import_default_agent()
    except RuntimeError as exc:
        log.error("daemon: cannot start: %s", exc)
        return 2
    try:
        config = AgentConfig()
        registry = ToolRegistry()  # empty: the standalone process has no DB
        agent = OpenClaude(config=config, tools=registry)
    except Exception as exc:
        log.error("daemon: cannot build OpenClaude: %s", exc)
        return 2
    daemon = OpenClaudeDaemon(queue=queue, agent=agent)
    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        log.info("daemon: interrupted, exiting")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
