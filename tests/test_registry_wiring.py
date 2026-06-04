"""Tests for the AppState / registry wiring (M3.7).

The dashboard provider list reads from the same registry the pipeline uses,
so this asserts that toggling VIBEDUMP_REGISTRY actually changes the API
output and the /api/providers health entries.
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
from vibedump.providers import fake_registry  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


def _client_with_registry(registry) -> TestClient:
    db = Database(":memory:")
    db.initialize()
    state = AppState(
        db=db,
        bus=EventBus(),
        pipeline=FakeAgentPipeline(db, registry=registry),
        memory=RagMemory(db),
    )
    return TestClient(create_app(state))


def test_default_registry_is_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBEDUMP_REGISTRY", raising=False)
    client = _client_with_registry(fake_registry())
    body = client.get("/api/providers").json()
    names = {h["name"] for h in body["health"]}
    # fake_registry now also includes the fake variants of the real
    # audio adapters (WhisperSTT.fake() / PiperTTS.fake()) so the
    # dashboard shows every selectable provider, even in zero-config.
    assert names == {"fake", "whisper_fake", "piper_fake"}


def test_real_registry_routes_through_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBEDUMP_REGISTRY", raising=False)
    from vibedump.providers import build_registry

    # Wipe cloud keys so the registry is deterministic - only local_pc survives.
    for var in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "MINIMAX_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("VIBEDUMP_PC_BASE_URL", raising=False)

    client = _client_with_registry(build_registry())
    body = client.get("/api/providers").json()
    names = {h["name"] for h in body["health"]}
    assert "local_pc" in names
    # fake STT + TTS stay registered regardless of LLM choice.
    stt_names = {h.name for h in client.app.state.vibedump.pipeline.registry.stt.values()}  # type: ignore[attr-defined]
    tts_names = {h.name for h in client.app.state.vibedump.pipeline.registry.tts.values()}  # type: ignore[attr-defined]
    assert stt_names == {"fake"}
    assert tts_names == {"fake"}


def test_create_dump_uses_pipeline_registry_stt() -> None:
    """POST /api/dumps with audio_path should transcribe via the wired STT."""
    client = _client_with_registry(fake_registry())
    res = client.post("/api/dumps", json={"title": "wired", "audio_path": "x.wav"})
    assert res.status_code == 201
    dump_id = res.json()["id"]
    turns = client.get(f"/api/dumps/{dump_id}/turns").json()
    assert turns["items"][0]["text"] == "Fake transcript for x.wav"
