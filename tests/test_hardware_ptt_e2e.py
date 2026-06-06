"""E2E push-to-talk flow on the fake hardware path (HW-14)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vibedump.app import AppState, create_app
from vibedump.agent_pipeline import AgentPipeline
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.integrations.audio_capture import FakeAudioCapture
from vibedump.integrations.audio_playback import FakeAudioPlayback
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS
from vibedump.ragmemory import RagMemory

pytest.importorskip("fastapi")


@dataclass
class _ScriptedLLM:
    name: str = "scripted"
    responses: list[str] = field(
        default_factory=lambda: ["[ASK] What platform are you targeting?"]
    )
    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted")


@pytest.fixture
def ptt_e2e_client() -> tuple[TestClient, AppState, FakeAudioPlayback]:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry: Any = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm["scripted"] = _ScriptedLLM()
    registry.tts["fake"] = FakeTTS()
    db.upsert_provider_config("scripted", "llm", True, {})
    playback = FakeAudioPlayback()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=registry, bus=bus),
        memory=RagMemory(db),
        audio_capture=FakeAudioCapture(),
        audio_playback=playback,
    )
    return TestClient(create_app(state)), state, playback


def test_ptt_e2e_start_complete_turn_and_listener(
    ptt_e2e_client: tuple[TestClient, AppState, FakeAudioPlayback],
) -> None:
    """PTT start → worker completes → user + assistant turns in DB + TTS fired."""
    client, state, playback = ptt_e2e_client
    dump_id = client.post("/api/dumps", json={"title": "E2E PTT"}).json()["id"]
    start = client.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 0.5},
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    deadline = time.time() + 5.0
    job_status = ""
    while time.time() < deadline:
        job_status = client.get(f"/api/hardware/ptt/{job_id}").json()["status"]
        if job_status in {"done", "error"}:
            break
        time.sleep(0.05)

    assert job_status == "done", f"PTT job ended with status={job_status!r}"

    turns = client.get(f"/api/dumps/{dump_id}/turns").json()["items"]
    roles = [t["role"] for t in turns]
    assert "user" in roles
    assert "assistant" in roles
    assert playback.last_pcm_len > 0 or playback.last_wav is not None
