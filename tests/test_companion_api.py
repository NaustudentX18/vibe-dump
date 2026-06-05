from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vibedump.app import AppState, create_app
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.agent_pipeline import FakeAgentPipeline
from vibedump.ragmemory import RagMemory

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture
def client_with_state() -> tuple[TestClient, AppState]:
    db = Database(":memory:")
    db.initialize()
    state = AppState(
        db=db,
        bus=EventBus(),
        pipeline=FakeAgentPipeline(db),
        memory=RagMemory(db),
    )
    return TestClient(create_app(state)), state


def test_companion_empty_database(client_with_state: tuple[TestClient, AppState]) -> None:
    client, _ = client_with_state
    
    # 1. GET cursor with empty database -> 404
    res_cursor = client.get("/api/companion/cursor")
    assert res_cursor.status_code == 404

    # 2. GET claudecode with empty database -> 404
    res_claudecode = client.get("/api/companion/claudecode")
    assert res_claudecode.status_code == 404


def test_companion_missing_blueprint(client_with_state: tuple[TestClient, AppState]) -> None:
    client, _ = client_with_state
    
    # 1. Create a dump (but no blueprint compiled yet)
    client.post("/api/dumps", json={"title": "No Blueprint Dump"})

    # 2. GET cursor -> 404 (blueprint not found)
    res_cursor = client.get("/api/companion/cursor")
    assert res_cursor.status_code == 404


def test_companion_success_endpoints(client_with_state: tuple[TestClient, AppState]) -> None:
    client, _ = client_with_state

    # 1. Create a dump and ingest fake to generate a blueprint
    create = client.post("/api/dumps", json={"title": "Vibe Rules"})
    assert create.status_code == 201
    dump_id = create.json()["id"]

    client.post(f"/api/dumps/{dump_id}/ingest-fake")

    # 2. GET cursor -> returns rules (for .cursorrules)
    res_cursor = client.get("/api/companion/cursor")
    assert res_cursor.status_code == 200
    data_cursor = res_cursor.json()
    assert "rules" in data_cursor
    assert "Vibe Coding Blueprint" in data_cursor["rules"]
    assert data_cursor["metadata"]["dump_id"] == dump_id

    # 3. GET claudecode -> returns context
    res_claudecode = client.get("/api/companion/claudecode")
    assert res_claudecode.status_code == 200
    data_claudecode = res_claudecode.json()
    assert "context" in data_claudecode
    assert "Vibe Coding Blueprint" in data_claudecode["context"]
    assert data_claudecode["metadata"]["dump_id"] == dump_id

    # 4. POST cursor -> OK
    res_post_cursor = client.post("/api/companion/cursor", json={"action": "build_workspace"})
    assert res_post_cursor.status_code == 200
    assert res_post_cursor.json() == {"status": "ok", "action_received": "build_workspace"}

    # 5. POST claudecode -> OK
    res_post_claudecode = client.post("/api/companion/claudecode", json={"action": "refresh_status"})
    assert res_post_claudecode.status_code == 200
    assert res_post_claudecode.json() == {"status": "ok", "action_received": "refresh_status"}
