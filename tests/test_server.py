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
from vibedump.agent_pipeline import AgentPipeline, FakeAgentPipeline  # noqa: E402
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


# ---------------------------------------------------------------------------
# M4: POST /api/dumps/{id}/turn (active-listener user turn)
# ---------------------------------------------------------------------------


def test_turn_route_unknown_dump_returns_404(client: TestClient) -> None:
    res = client.post("/api/dumps/9999/turn", json={"text": "hi"})
    assert res.status_code == 404


def test_turn_route_malformed_llm_output_returns_502(client: TestClient) -> None:
    """The default fake LLM returns prose without [ASK]/[FINALIZE].

    The route should surface that as 502 (bad gateway) so the dashboard
    can show a meaningful error rather than a generic 500.
    """
    dump_id = client.post("/api/dumps", json={"title": "M4 turn"}).json()["id"]
    res = client.post(f"/api/dumps/{dump_id}/turn", json={"text": "go on, ask me"})
    assert res.status_code == 502
    assert "must start with" in res.json()["detail"]


def test_turn_route_publishes_user_turn_event(client: TestClient) -> None:
    """The user-turn event is what the live-transcript UI listens for."""
    state: AppState = client.app.state.vibedump  # type: ignore[attr-defined]
    snapshot_before = len(state.bus.snapshot())
    dump_id = client.post("/api/dumps", json={"title": "events"}).json()["id"]
    client.post(f"/api/dumps/{dump_id}/turn", json={"text": "go on, ask me"})
    user_turn_events = [
        e for e in state.bus.snapshot()
        if e.event_type == "dump.user_turn" and e.payload.get("dump_id") == dump_id
    ]
    assert len(user_turn_events) == 1
    assert user_turn_events[0].payload["text"] == "go on, ask me"
    assert len(state.bus.snapshot()) > snapshot_before


def test_turn_route_happy_path_ask_then_finalize() -> None:
    """Full happy path: scripted LLM, [ASK] then user turn then [FINALIZE]."""
    from dataclasses import dataclass, field
    from typing import Any

    from vibedump.providers import ProviderRegistry
    from vibedump.providers.base import ProviderHealth
    from vibedump.providers.stt import FakeSTT
    from vibedump.providers.tts import FakeTTS
    from vibedump.schemas import blueprint_template

    @dataclass
    class _ScriptedLLM:
        name: str = "scripted"
        configured: bool = True
        calls: list[str] = field(default_factory=list)
        responses: list[str] = field(
            default_factory=lambda: [
                "[ASK] what platform?",
                "[FINALIZE] enough info",
                blueprint_template("M4 turn"),
            ]
        )

        def complete(self, prompt: str) -> str:
            self.calls.append(prompt)
            return self.responses.pop(0)

        def health(self) -> ProviderHealth:
            return ProviderHealth(self.name, True, "scripted")

    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry: Any = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    llm_stub: Any = _ScriptedLLM()
    registry.llm["scripted"] = llm_stub
    registry.tts["fake"] = FakeTTS()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry, bus=bus, llm=llm_stub),
        memory=RagMemory(db),
    )

    c = TestClient(create_app(state))
    dump_id = c.post("/api/dumps", json={"title": "M4 turn", "audio_path": "idea.wav"}).json()["id"]

    # First user turn: listener asks a question.
    r1 = c.post(f"/api/dumps/{dump_id}/turn", json={"text": "iOS first"})
    assert r1.status_code == 201
    body1 = r1.json()
    assert body1["action"] == "ask"
    assert body1["assistant_text"] == "what platform?"
    assert body1["status"] == "listening"
    assert body1["blueprint"] is None

    # Second user turn: listener finalizes and compiles the blueprint.
    r2 = c.post(f"/api/dumps/{dump_id}/turn", json={"text": "iOS, then Android"})
    assert r2.status_code == 201
    body2 = r2.json()
    assert body2["action"] == "finalize"
    assert body2["status"] == "ready"
    assert body2["blueprint"] is not None
    assert "Vibe Coding Blueprint" in body2["blueprint"]

    # The LLM saw both user turns and the prior assistant question.
    assert "what platform?" in llm_stub.calls[1]
    assert "iOS first" in llm_stub.calls[1]
