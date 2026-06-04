"""Tests for the M9 agent HTTP routes.

These tests cover the five documented endpoints:

* POST /api/agent/run
* GET  /api/agent/jobs
* GET  /api/agent/jobs/{id}
* POST /api/agent/jobs/{id}/cancel
* POST /api/agent/jobs/{id}/retry
* GET  /api/agent/health

The pre-assumed ``OpenClaude`` / ``ToolRegistry`` / ``build_default_registry``
modules are not yet on disk, so the AppState exposes only a real
``JobQueue`` (which the routes depend on directly). The daemon
runtime is left ``None`` so the ``daemon=False`` health shape is
exercised.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vibedump.agent.queue import JobQueue  # noqa: E402
from vibedump.agent_pipeline import AgentPipeline  # noqa: E402
from vibedump.app import AppState, create_app  # noqa: E402
from vibedump.database import Database  # noqa: E402
from vibedump.events import EventBus  # noqa: E402
from vibedump.providers import fake_registry  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


@pytest.fixture
def app_state(tmp_path: Path) -> AppState:
    """Wire an AppState with a real JobQueue against a tmp file DB."""
    db_path = tmp_path / "agent.sqlite3"
    db = Database(db_path)
    db.initialize()
    bus = EventBus()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=fake_registry(), bus=bus),
        memory=RagMemory(db),
    )
    # Inject just enough of the agent runtime for the HTTP routes to
    # work: a real JobQueue, no daemon, no registry. The route contract
    # only depends on the queue.
    state.agent_queue = JobQueue(db_path)
    state.agent = object()  # marker so the health route can answer
    state.agent_registry = None
    state.agent_daemon = None
    state.agent_daemon_thread = None
    state.agent_runtime_error = None
    return state


@pytest.fixture
def client(app_state: AppState) -> TestClient:
    return TestClient(create_app(app_state))


# ---------------------------------------------------------------------------
# /api/agent/run
# ---------------------------------------------------------------------------


def test_agent_run_enqueues_job(client: TestClient) -> None:
    """A valid prompt creates a pending job and returns its id."""
    res = client.post(
        "/api/agent/run",
        json={"prompt": "summarise this", "kind": "summarise", "priority": 1},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert isinstance(body["job_id"], str) and body["job_id"]


def test_agent_run_rejects_empty_prompt(client: TestClient) -> None:
    """A whitespace-only or empty prompt yields 400."""
    res = client.post("/api/agent/run", json={"prompt": ""})
    assert res.status_code == 422  # Pydantic min_length=1
    res = client.post("/api/agent/run", json={"prompt": "   \n   "})
    assert res.status_code == 400
    assert "prompt" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/agent/jobs
# ---------------------------------------------------------------------------


def test_agent_jobs_returns_list(client: TestClient) -> None:
    """Listing jobs returns the items + a counts dict."""
    client.post("/api/agent/run", json={"prompt": "one"})
    client.post("/api/agent/run", json={"prompt": "two"})
    res = client.get("/api/agent/jobs")
    assert res.status_code == 200
    body = res.json()
    assert "items" in body and "counts" in body
    assert len(body["items"]) >= 2
    assert body["counts"]["pending"] >= 2
    for job in body["items"]:
        assert "id" in job
        assert job["status"] == "pending"


def test_agent_jobs_filters_by_status(client: TestClient) -> None:
    """The ``?status=`` query param narrows the list to a single status."""
    client.post("/api/agent/run", json={"prompt": "a"})
    res = client.get("/api/agent/jobs?status=pending")
    assert res.status_code == 200
    body = res.json()
    assert all(j["status"] == "pending" for j in body["items"])
    # Asking for an empty status should return zero items.
    res = client.get("/api/agent/jobs?status=complete")
    body = res.json()
    assert body["items"] == []


# ---------------------------------------------------------------------------
# /api/agent/jobs/{id}
# ---------------------------------------------------------------------------


def test_agent_job_get_returns_record(client: TestClient) -> None:
    """Fetching a known job returns its full record."""
    created = client.post(
        "/api/agent/run", json={"prompt": "inspect me", "kind": "inspect"}
    ).json()
    res = client.get(f"/api/agent/jobs/{created['job_id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == created["job_id"]
    assert body["prompt"] == "inspect me"
    assert body["kind"] == "inspect"
    assert body["status"] == "pending"


def test_agent_job_get_unknown_404(client: TestClient) -> None:
    """Fetching an unknown id yields 404."""
    res = client.get("/api/agent/jobs/deadbeefdeadbeef")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/agent/jobs/{id}/cancel
# ---------------------------------------------------------------------------


def test_agent_job_cancel_unknown_404(client: TestClient) -> None:
    """Cancelling an unknown job returns 404."""
    res = client.post("/api/agent/jobs/nope-not-real/cancel")
    assert res.status_code == 404


def test_agent_job_cancel_pending_succeeds(client: TestClient) -> None:
    """Cancelling a pending job marks it cancelled."""
    created = client.post(
        "/api/agent/run", json={"prompt": "abort me"}
    ).json()
    res = client.post(f"/api/agent/jobs/{created['job_id']}/cancel")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "cancelled"
    # The DB row reflects the new status.
    res = client.get(f"/api/agent/jobs/{created['job_id']}")
    assert res.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# /api/agent/jobs/{id}/retry
# ---------------------------------------------------------------------------


def test_agent_job_retry_creates_new_job(client: TestClient) -> None:
    """Retrying a failed job enqueues a fresh pending job."""
    created = client.post(
        "/api/agent/run", json={"prompt": "retry me", "kind": "retry", "priority": 2}
    ).json()
    queue = client.get("/api/agent/jobs").json()["items"]
    job_id = next(j["id"] for j in queue if j["id"] == created["job_id"])
    # Force the job into a failed state by writing through the queue.
    app_state: AppState = client.app.state.vibedump  # type: ignore[attr-defined]
    app_state.agent_queue.fail(job_id, "boom", retry=False)
    res = client.post(f"/api/agent/jobs/{job_id}/retry")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert body["retry_of"] == job_id
    assert body["job_id"] != job_id
    # The new job is visible in the list.
    res = client.get("/api/agent/jobs")
    ids = [j["id"] for j in res.json()["items"]]
    assert body["job_id"] in ids
    assert job_id in ids  # original still around


def test_agent_job_retry_rejects_pending(client: TestClient) -> None:
    """Retrying a still-pending job is rejected (409)."""
    created = client.post("/api/agent/run", json={"prompt": "fresh"}).json()
    res = client.post(f"/api/agent/jobs/{created['job_id']}/retry")
    assert res.status_code == 409
    assert "pending" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/agent/health
# ---------------------------------------------------------------------------


def test_agent_health_returns_shape(client: TestClient) -> None:
    """Health returns the documented envelope, including counts and tools."""
    res = client.get("/api/agent/health")
    assert res.status_code == 200
    body = res.json()
    for key in ("daemon", "queue_pending", "queue_claimed", "tools"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["daemon"], bool)
    assert isinstance(body["queue_pending"], int)
    assert isinstance(body["queue_claimed"], int)
    assert isinstance(body["tools"], list)
    # No daemon was started in this test client, so daemon is False.
    assert body["daemon"] is False
