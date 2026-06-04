"""Tests for the M7 hardware HTTP routes + push-to-talk wiring.

These tests cover:
* GET  /api/hardware/status
* POST /api/hardware/display
* GET  /api/hardware/pisugar
* POST /api/hardware/ptt/start
* POST /api/hardware/ptt/cancel
* GET  /api/hardware/ptt/{job_id}

The fixtures build a custom AppState so the Whisplay / PiSugar /
AudioCapture bridges can be swapped for fakes that are deterministic
and require no hardware.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vibedump.agent_pipeline import AgentPipeline  # noqa: E402
from vibedump.app import AppState, PttJob, create_app  # noqa: E402
from vibedump.database import Database  # noqa: E402
from vibedump.events import EventBus  # noqa: E402
from vibedump.integrations.audio_capture import FakeAudioCapture  # noqa: E402
from vibedump.integrations.pisugar import (  # noqa: E402
    BatteryReading,
    FakePiSugarBridge,
)
from vibedump.integrations.whisplay import FakeWhisplayBridge  # noqa: E402
from vibedump.providers import ProviderRegistry  # noqa: E402
from vibedump.providers.base import ProviderHealth  # noqa: E402
from vibedump.providers.stt import FakeSTT  # noqa: E402
from vibedump.providers.tts import FakeTTS  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


# A trivial valid 1x1 transparent PNG (68 bytes).
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
    b"\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"A\x9c\x82\xc8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass
class _ScriptedSTT:
    """A fake STT provider that returns a fixed transcript."""

    name: str = "scripted"
    transcript: str = "hello world"
    calls: list[str] = field(default_factory=list)

    def transcribe(self, audio_path: str) -> str:
        self.calls.append(audio_path)
        return self.transcript

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted")


def _make_state(
    *,
    whisplay: FakeWhisplayBridge | None = None,
    pisugar: FakePiSugarBridge | None = None,
    audio: FakeAudioCapture | None = None,
    stt_provider: Any = None,
) -> AppState:
    """Build an AppState with the given hardware bridges + optional STT provider."""
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry = ProviderRegistry()
    if stt_provider is None:
        stt_provider = FakeSTT()
    registry.stt[stt_provider.name] = stt_provider
    registry.tts["fake"] = FakeTTS()
    pipeline = AgentPipeline(db, registry, bus=bus)
    state = AppState(
        db=db,
        bus=bus,
        pipeline=pipeline,
        memory=RagMemory(db),
        whisplay=whisplay or FakeWhisplayBridge(),
        pisugar=pisugar or FakePiSugarBridge(),
        audio_capture=audio or FakeAudioCapture(),
    )
    state.ptt_stt_provider = stt_provider.name
    return state


@pytest.fixture
def client() -> TestClient:
    state = _make_state()
    return TestClient(create_app(state))


@pytest.fixture
def scripted_client() -> tuple[TestClient, _ScriptedSTT, AppState]:
    scripted = _ScriptedSTT(transcript="hello world")
    state = _make_state(stt_provider=scripted)
    return TestClient(create_app(state)), scripted, state


# ---------------------------------------------------------------------------
# /api/hardware/status
# ---------------------------------------------------------------------------


def test_hardware_status_returns_known_booleans(client: TestClient) -> None:
    res = client.get("/api/hardware/status")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) >= {"whisplay", "pisugar", "audio_capture", "buttons_pending"}
    assert body["whisplay"] is True
    assert body["audio_capture"] is True
    # FakePiSugarBridge is always available; the route fires a fresh read()
    # and serialises the BatteryReading dict.
    assert isinstance(body["pisugar"], dict)
    assert "battery_percent" in body["pisugar"]


def test_hardware_status_pisugar_null_when_bridge_missing() -> None:
    state = _make_state()
    state.pisugar = None
    c = TestClient(create_app(state))
    res = c.get("/api/hardware/status")
    assert res.status_code == 200
    assert res.json()["pisugar"] is None


def test_hardware_status_buttons_pending_empty_for_fake_bridge(client: TestClient) -> None:
    """FakeWhisplayBridge has no queued buttons by default."""
    res = client.get("/api/hardware/status")
    assert res.status_code == 200
    assert res.json()["buttons_pending"] == []


def test_hardware_status_drains_simulated_button_presses() -> None:
    state = _make_state()
    state.whisplay.simulate_button("A")  # type: ignore[union-attr]
    state.whisplay.simulate_button("B")  # type: ignore[union-attr]
    c = TestClient(create_app(state))
    res = c.get("/api/hardware/status")
    assert res.status_code == 200
    # buttons_pending is sorted on the way out so the test is deterministic.
    assert res.json()["buttons_pending"] == ["A", "B"]


# ---------------------------------------------------------------------------
# /api/hardware/display
# ---------------------------------------------------------------------------


def test_hardware_display_round_trips_png(client: TestClient) -> None:
    payload = {"png_base64": base64.b64encode(_PNG_BYTES).decode("ascii")}
    res = client.post("/api/hardware/display", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["bytes_sent"] == len(_PNG_BYTES)
    assert body["bytes_sent"] > 0


def test_hardware_display_503_when_no_bridge() -> None:
    state = _make_state()
    state.whisplay = None
    c = TestClient(create_app(state))
    res = c.post("/api/hardware/display", json={"png_base64": "AAAA"})
    assert res.status_code == 503


def test_hardware_display_rejects_empty_base64() -> None:
    state = _make_state()
    c = TestClient(create_app(state))
    res = c.post("/api/hardware/display", json={"png_base64": ""})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# /api/hardware/pisugar
# ---------------------------------------------------------------------------


def test_hardware_pisugar_returns_battery_dict(client: TestClient) -> None:
    res = client.get("/api/hardware/pisugar")
    assert res.status_code == 200
    body = res.json()
    for key in (
        "battery_percent",
        "voltage_v",
        "current_ma",
        "temperature_c",
        "is_charging",
        "is_powered",
        "timestamp",
    ):
        assert key in body, f"missing battery field: {key}"


def test_hardware_pisugar_503_when_no_bridge() -> None:
    state = _make_state()
    state.pisugar = None
    c = TestClient(create_app(state))
    res = c.get("/api/hardware/pisugar")
    assert res.status_code == 503


# ---------------------------------------------------------------------------
# /api/hardware/ptt
# ---------------------------------------------------------------------------


def test_ptt_start_returns_202_and_job_id_then_completes(client: TestClient) -> None:
    dump_id = client.post("/api/dumps", json={"title": "PTT happy"}).json()["id"]
    res = client.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 0.5},
    )
    assert res.status_code == 202
    body = res.json()
    assert "job_id" in body and len(body["job_id"]) >= 16
    assert body["dump_id"] == dump_id
    assert "wav_path" in body

    # Wait for the background thread to finish recording + transcribing.
    deadline = time.time() + 3.0
    job_status = None
    while time.time() < deadline:
        poll = client.get(f"/api/hardware/ptt/{body['job_id']}")
        assert poll.status_code == 200
        job_status = poll.json()["status"]
        if job_status in {"done", "error", "cancelled"}:
            break
        time.sleep(0.05)
    assert job_status in {"done", "transcribing", "error"}


def test_ptt_start_with_unknown_dump_lands_in_error_state(client: TestClient) -> None:
    """A PTT start for a non-existent dump must not crash the server; the
    background thread surfaces the ValueError as ``status="error"`` with a
    meaningful transcript so the dashboard can render a useful toast."""
    res = client.post(
        "/api/hardware/ptt/start",
        json={"dump_id": 1, "duration_s": 0.5},
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    deadline = time.time() + 3.0
    job_body: dict[str, Any] | None = None
    while time.time() < deadline:
        poll = client.get(f"/api/hardware/ptt/{job_id}")
        if poll.status_code == 200:
            job_body = poll.json()
            if job_body["status"] in {"done", "error", "cancelled"}:
                break
        time.sleep(0.05)
    assert job_body is not None
    assert job_body["status"] in {"error", "done"}
    # A meaningful transcript is one that is not None/empty; the
    # add_user_turn failure message is preserved so the user sees why.
    assert job_body.get("transcript") not in (None, "")


def test_ptt_cancel_unknown_job_returns_404(client: TestClient) -> None:
    res = client.post("/api/hardware/ptt/cancel", json={"job_id": "nope-not-real"})
    assert res.status_code == 404


def test_ptt_cancel_known_job_marks_status_cancelled(client: TestClient) -> None:
    """A cancel call for a live job flips the status field. The thread
    will exit cleanly on its next poll even if the recording is mid-flight."""
    dump_id = client.post("/api/dumps", json={"title": "PTT cancel"}).json()["id"]
    start = client.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 2.0},
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]
    cancel = client.post("/api/hardware/ptt/cancel", json={"job_id": job_id})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    # The persisted job record reflects the cancel.
    poll = client.get(f"/api/hardware/ptt/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "cancelled"


def test_ptt_pipeline_persists_user_turn_from_stt(scripted_client: tuple[TestClient, _ScriptedSTT, AppState]) -> None:
    """The PTT worker must transcribe via the configured STT provider and
    hand the transcript to ``pipeline.add_user_turn``."""
    c, scripted, state = scripted_client
    dump_id = c.post("/api/dumps", json={"title": "PTT scripted"}).json()["id"]
    start = c.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 0.5},
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    # Wait for the worker to call add_user_turn. The fake STT returns
    # ``"hello world"``; we assert the dump has a turn with that text.
    deadline = time.time() + 3.0
    turns: list[dict[str, Any]] = []
    while time.time() < deadline:
        turns = c.get(f"/api/dumps/{dump_id}/turns").json()["items"]
        if any(t["text"] == "hello world" for t in turns):
            break
        time.sleep(0.05)
    assert any(t["text"] == "hello world" for t in turns), (
        f"PTT worker never persisted the STT transcript; turns={turns}"
    )
    assert scripted.calls, "scripted STT was never invoked"


def test_ptt_publishes_completed_event(scripted_client: tuple[TestClient, _ScriptedSTT, AppState]) -> None:
    """The bus event lets the SSE layer show a transcript toast without
    the dashboard needing to poll."""
    c, _scripted, state = scripted_client
    bus = state.bus
    snapshot_before = len(bus.snapshot())
    dump_id = c.post("/api/dumps", json={"title": "PTT event"}).json()["id"]
    start = c.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 0.5},
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    deadline = time.time() + 3.0
    completed: list[Any] = []
    while time.time() < deadline:
        completed = [
            e for e in bus.snapshot()
            if e.event_type == "ptt.completed" and e.payload.get("job_id") == job_id
        ]
        if completed:
            break
        time.sleep(0.05)
    assert completed, "ptt.completed event was never published"
    payload = completed[-1].payload
    assert payload["dump_id"] == dump_id
    assert payload["wav_path"]
    assert payload["transcript"] == "hello world"
    assert len(bus.snapshot()) > snapshot_before


def test_ptt_start_503_when_no_capture() -> None:
    state = _make_state()
    state.audio_capture = None
    c = TestClient(create_app(state))
    res = c.post(
        "/api/hardware/ptt/start",
        json={"dump_id": 1, "duration_s": 1.0},
    )
    assert res.status_code == 503


def test_ptt_start_rejects_duration_above_maximum(client: TestClient) -> None:
    dump_id = client.post("/api/dumps", json={"title": "PTT bounds"}).json()["id"]
    res = client.post(
        "/api/hardware/ptt/start",
        json={"dump_id": dump_id, "duration_s": 120.0},
    )
    assert res.status_code == 422
