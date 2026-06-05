from __future__ import annotations

import os
import wave
import pytest
from dataclasses import dataclass, field
from typing import Any
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from vibedump.app import AppState, create_app
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.agent_pipeline import AgentPipeline
from vibedump.ragmemory import RagMemory
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@dataclass
class ScriptedLLM:
    name: str = "scripted"
    configured: bool = True
    calls: list[str] = field(default_factory=list)
    responses: list[str] = field(
        default_factory=lambda: [
            "[ASK] what platform?",
            "[FINALIZE] enough info",
        ]
    )

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted")


@pytest.fixture
def client_with_state() -> tuple[TestClient, AppState]:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry: Any = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    llm_stub: Any = ScriptedLLM()
    registry.llm["scripted"] = llm_stub
    registry.tts["fake"] = FakeTTS()
    
    # Write a config row so select_llm_provider picks our scripted LLM
    db.upsert_provider_config("scripted", "llm", True, {})

    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=registry, bus=bus),
        memory=RagMemory(db),
    )
    return TestClient(create_app(state)), state


def test_audio_stream_missing_dump(client_with_state: tuple[TestClient, AppState]) -> None:
    client, _ = client_with_state
    # Connecting with a dump_id that does not exist should raise WebSocketDisconnect on receive
    with client.websocket_connect("/api/audio/stream?dump_id=999") as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_bytes()
        assert exc_info.value.code == 1008  # status.WS_1008_POLICY_VIOLATION


def test_audio_stream_success(client_with_state: tuple[TestClient, AppState]) -> None:
    client, state = client_with_state
    
    # 1. Create a dump
    create = client.post("/api/dumps", json={"title": "Test Stream Dump"})
    assert create.status_code == 201
    dump_id = create.json()["id"]

    # 2. Stream some audio data
    with client.websocket_connect(f"/api/audio/stream?dump_id={dump_id}") as websocket:
        websocket.send_bytes(b"\x00\x00" * 8000)  # 0.5s of 16kHz mono audio
        websocket.send_bytes(b"\x00\x00" * 8000)

    # 3. Verify turn was added to the database
    turns = state.db.list_turns(dump_id)
    # The streamed user turn
    # And the assistant's response turn triggered by the pipeline step_listener
    assert len(turns) >= 2
    user_turn = [t for t in turns if t.role == "user"][0]
    assert user_turn.audio_path is not None
    assert os.path.exists(user_turn.audio_path)

    # Verify WAV file is a valid WAV
    with wave.open(user_turn.audio_path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000

    # Clean up the file
    try:
        os.unlink(user_turn.audio_path)
    except OSError:
        pass

    # 4. Verify SSE event for user turn was published
    events = state.bus.snapshot()
    user_turn_events = [e for e in events if e.event_type == "dump.user_turn" and e.payload.get("dump_id") == dump_id]
    assert len(user_turn_events) > 0
