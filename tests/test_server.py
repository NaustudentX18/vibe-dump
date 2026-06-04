"""Tests for the FastAPI web surface.

These tests use FastAPI's TestClient. The FastAPI/uvicorn dependencies are
optional; if they are missing the tests are skipped.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vibedump.app import AppState, create_app  # noqa: E402
from vibedump.database import Database  # noqa: E402
from vibedump.events import EventBus  # noqa: E402
from vibedump.agent_pipeline import FakeAgentPipeline  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    db = Database(":memory:")
    db.initialize()
    state = AppState(
        db=db,
        bus=EventBus(),
        pipeline=FakeAgentPipeline(db),
        memory=RagMemory(db),
    )
    return TestClient(create_app(state))


def test_health_and_index(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    index = client.get("/")
    assert index.status_code == 200
    assert "<title>Vibe-Dump" in index.text


def test_dump_crud_lifecycle(client: TestClient) -> None:
    create = client.post("/api/dumps", json={"title": "Pocket Manus", "audio_path": "a.wav"})
    assert create.status_code == 201
    dump = create.json()
    dump_id = dump["id"]
    assert dump["title"] == "Pocket Manus"
    assert dump["status"] == "draft"

    listed = client.get("/api/dumps").json()
    assert any(d["id"] == dump_id for d in listed["items"])

    fetched = client.get(f"/api/dumps/{dump_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Pocket Manus"

    turns = client.get(f"/api/dumps/{dump_id}/turns").json()
    assert len(turns["items"]) >= 1
    assert turns["items"][0]["role"] == "user"
    assert "a.wav" in turns["items"][0]["text"]

    not_found = client.get("/api/dumps/9999")
    assert not_found.status_code == 404

    deleted = client.delete(f"/api/dumps/{dump_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/dumps/{dump_id}").status_code == 404


def test_ingest_fake_creates_blueprint(client: TestClient) -> None:
    dump_id = client.post("/api/dumps", json={"title": "Spec"}).json()["id"]
    res = client.post(f"/api/dumps/{dump_id}/ingest-fake")
    assert res.status_code == 201
    body = res.json()
    assert "blueprint" in body
    assert "Vibe Coding Blueprint" in body["blueprint"]

    bp = client.get(f"/api/dumps/{dump_id}/blueprint").json()
    assert "## 1." in bp["markdown"]


def test_status_update_changes_state(client: TestClient) -> None:
    dump_id = client.post("/api/dumps", json={"title": "Status test"}).json()["id"]
    res = client.patch(f"/api/dumps/{dump_id}/status", json={"status": "thinking"})
    assert res.status_code == 200
    assert res.json()["status"] == "thinking"
    assert client.get(f"/api/dumps/{dump_id}").json()["status"] == "thinking"


def test_search_returns_fts_results(client: TestClient) -> None:
    dump_id = client.post("/api/dumps", json={"title": "Searchable", "audio_path": "x.wav"}).json()["id"]
    client.post(f"/api/dumps/{dump_id}/ingest-fake")
    res = client.get("/api/search", params={"q": "Searchable"}).json()
    assert res["query"] == "Searchable"
    assert res["results"]
    assert any(r["dump_id"] == dump_id for r in res["results"])


def test_search_rejects_blank_query(client: TestClient) -> None:
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_providers_health_and_upsert(client: TestClient) -> None:
    initial = client.get("/api/providers").json()
    assert initial["health"]  # fake providers are registered
    assert initial["configs"] == []

    upsert = client.post(
        "/api/providers",
        json={"name": "openrouter", "kind": "llm", "enabled": True, "config": {"model": "x"}},
    )
    assert upsert.status_code == 201
    pid = upsert.json()["id"]

    second = client.post(
        "/api/providers",
        json={"name": "openrouter", "kind": "llm", "enabled": False, "config": {"model": "y"}},
    )
    assert second.json()["id"] == pid  # upsert preserves id

    listed = client.get("/api/providers").json()
    assert any(c["name"] == "openrouter" and c["enabled"] is False for c in listed["configs"])


def test_events_sse_streams_history(client: TestClient) -> None:
    state: AppState = client.app.state.vibedump  # type: ignore[attr-defined]
    state.bus.publish("dump.created", {"dump_id": 7, "title": "SSE check"})
    state.bus.publish("dump.deleted", {"dump_id": 7})

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text() if chunk)
    assert "dump.created" in body
    assert "dump.deleted" in body


def test_validation_error_for_blank_title(client: TestClient) -> None:
    res = client.post("/api/dumps", json={"title": "   "})
    assert res.status_code == 400
