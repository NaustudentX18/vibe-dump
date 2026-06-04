"""App factory for the Vibe-Dump web dashboard.

Builds a FastAPI app with:
- A single shared in-process state (DB, event bus, fake pipeline).
- Mobile-first dashboard served at ``/``.
- JSON API for dumps, search, providers, and SSE for live updates.

FastAPI is an optional dependency. The factory raises a clear error if it is
not installed so the package still imports cleanly for the database / RAG tests.
"""

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Iterator
from uuid import uuid4

from .agent_pipeline import AgentPipeline
from .database import (
    AchievementRecord,
    BlueprintRecord,
    Database,
    DumpRecord,
    ProfileRecord,
    ProviderConfigRecord,
    TurnRecord,
)
from .events import EventBus
from .integrations.audio_capture import (
    AudioCapture,
    AudioCaptureNotAvailable,
    make_audio_capture,
)
from .integrations.pisugar import (
    BatteryReading,
    PiSugarBridge,
    PiSugarMonitor,
    PiSugarNotAvailable,
    make_pisugar,
)
from .integrations.whisplay import (
    WhisplayBridge,
    WhisplayNotAvailable,
    make_whisplay,
)
from .mascot_renderer import MascotRenderer
from .providers import build_registry, fake_registry
from .ragmemory import RagMemory
from .state import DeviceState

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"

DEFAULT_API_PREFIX = "/api"

# M7 push-to-talk: write WAVs here so the STT layer can find them.
PTT_TMP_DIR = Path(os.environ.get("VIBEDUMP_PTT_DIR", "/tmp"))


@dataclass(slots=True)
class PttJob:
    """In-process tracker for a single push-to-talk recording.

    Statuses:
    * ``"recording"``    - audio capture in flight
    * ``"transcribing"`` - WAV recorded, STT call running
    * ``"done"``         - transcript persisted as a user turn
    * ``"cancelled"``    - ``/api/hardware/ptt/cancel`` fired
    * ``"error"``        - any exception during record/transcribe/add_turn
    """

    job_id: str
    dump_id: int
    wav_path: str
    started_at: str
    status: str
    transcript: str | None = None
    error: str | None = None


@dataclass(slots=True)
class AppState:
    """Shared singletons for a Vibe-Dump web process."""

    db: Database
    bus: EventBus
    pipeline: AgentPipeline
    memory: RagMemory
    device_state: DeviceState = DeviceState.IDLE
    mascot_renderer: MascotRenderer = field(default_factory=MascotRenderer)
    # M7 hardware bridges. ``None`` means the integration is unavailable on
    # this host (driver missing, I2C bus absent, no microphone, etc.).
    whisplay: WhisplayBridge | None = None
    pisugar: PiSugarBridge | None = None
    pisugar_monitor: PiSugarMonitor | None = None
    audio_capture: AudioCapture | None = None
    # STT provider name used by the PTT route. Defaults to ``"fake"`` so the
    # test suite (and zero-config dev) can run without a cloud STT key.
    ptt_stt_provider: str = "fake"
    # In-process PTT job registry. Keyed by ``uuid4().hex``.
    ptt_jobs: dict[str, PttJob] = field(default_factory=dict)
    ptt_lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        if self.pisugar_monitor is not None:
            try:
                self.pisugar_monitor.stop()
            except Exception:
                pass
        # Hardware bridges expose ``close`` defensively; the new
        # ``AudioCapture`` Protocol does not require it. ``getattr`` keeps
        # this code working for both the Whisplay/PiSugar bridges and the
        # arecord-backed AudioCapture.
        for bridge in (self.whisplay, self.pisugar):
            if bridge is None:
                continue
            close = getattr(bridge, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if self.audio_capture is not None:
            try:
                self.audio_capture.cancel()
            except Exception:
                pass
        self.db.close()


def _create_default_state() -> AppState:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    # VIBEDUMP_REGISTRY=fake (default) keeps tests + zero-config dev hermetic.
    # VIBEDUMP_REGISTRY=real wires the production provider set (build_registry).
    registry_kind = os.environ.get("VIBEDUMP_REGISTRY", "fake").lower()
    registry = build_registry() if registry_kind == "real" else fake_registry()
    state = AppState(
        db=db,
        bus=bus,
        pipeline=AgentPipeline(db, registry=registry, bus=bus),
        memory=RagMemory(db),
    )
    # M7 hardware. Each ``make_*`` is best-effort: missing drivers fall back
    # to fake, missing fake constructors raise. We swallow WhisplayNotAvailable
    # / PiSugarNotAvailable / AudioCaptureNotAvailable and leave the field
    # ``None`` so the app starts even on a stock dev box.
    try:
        state.whisplay = make_whisplay(prefer="auto")
    except WhisplayNotAvailable:
        state.whisplay = None
    try:
        state.pisugar = make_pisugar(prefer="auto")
    except PiSugarNotAvailable:
        state.pisugar = None
    try:
        state.audio_capture = make_audio_capture(prefer="auto")
    except AudioCaptureNotAvailable:
        state.audio_capture = None
    return state


def _dump_to_dict(dump: DumpRecord) -> dict[str, Any]:
    return {
        "id": dump.id,
        "title": dump.title,
        "status": dump.status,
        "created_at": dump.created_at,
        "updated_at": dump.updated_at,
        "metadata": dump.metadata,
    }


def _turn_to_dict(turn: TurnRecord) -> dict[str, Any]:
    return {
        "id": turn.id,
        "dump_id": turn.dump_id,
        "role": turn.role,
        "text": turn.text,
        "audio_path": turn.audio_path,
        "created_at": turn.created_at,
    }


def _blueprint_to_dict(blueprint: BlueprintRecord) -> dict[str, Any]:
    return {
        "id": blueprint.id,
        "dump_id": blueprint.dump_id,
        "markdown": blueprint.markdown,
        "created_at": blueprint.created_at,
    }


def _provider_to_dict(config: ProviderConfigRecord) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "kind": config.kind,
        "enabled": config.enabled,
        "config": config.config,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


def _achievement_to_dict(achievement: AchievementRecord) -> dict[str, Any]:
    return {
        "key": achievement.key,
        "title": achievement.title,
        "description": achievement.description,
        "unlocked_at": achievement.unlocked_at,
    }


def _profile_to_dict(profile: ProfileRecord) -> dict[str, Any]:
    return {
        "name": profile.name,
        "xp": profile.xp,
        "level": profile.level,
        "streak_days": profile.streak_days,
        "xp_to_next_level": 100 - (profile.xp % 100),
    }


def _battery_to_dict(reading: BatteryReading) -> dict[str, Any]:
    return {
        "battery_percent": reading.battery_percent,
        "voltage_v": reading.voltage_v,
        "current_ma": reading.current_ma,
        "temperature_c": reading.temperature_c,
        "is_charging": reading.is_charging,
        "is_powered": reading.is_powered,
        "timestamp": reading.timestamp,
    }


def create_app(state: AppState | None = None) -> Any:
    """Create the FastAPI app, wiring routes to the provided state."""
    try:
        from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
        from fastapi.responses import HTMLResponse, StreamingResponse
        from pydantic import BaseModel, Field
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dep path
        raise RuntimeError("Install vibe-dump[web] to use the web app") from exc

    app_state = state or _create_default_state()
    app = FastAPI(title="Vibe-Dump")
    app.state.vibedump = app_state

    class CreateDumpRequest(BaseModel):
        title: str = Field(min_length=1, max_length=200)
        audio_path: str | None = None
        metadata: dict[str, Any] = Field(default_factory=dict)

    class AddTurnRequest(BaseModel):
        text: str = Field(min_length=1, max_length=4000)

    class UpdateStatusRequest(BaseModel):
        status: str = Field(min_length=1, max_length=40)

    class ProviderUpsertRequest(BaseModel):
        name: str = Field(min_length=1, max_length=80)
        kind: str = Field(min_length=1, max_length=20)
        enabled: bool = True
        config: dict[str, Any] = Field(default_factory=dict)

    class ProfilePatchRequest(BaseModel):
        name: str | None = Field(default=None, max_length=80)
        xp_delta: int | None = None

    class DisplayFrameRequest(BaseModel):
        png_base64: str = Field(min_length=1, max_length=200_000)
        width: int = Field(default=240, ge=1, le=480)
        height: int = Field(default=280, ge=1, le=480)

    class PttStartRequest(BaseModel):
        dump_id: int = Field(ge=1)
        duration_s: float = Field(default=5.0, ge=0.5, le=60.0)

    class PttCancelRequest(BaseModel):
        job_id: str = Field(min_length=1, max_length=64)

    def get_state(request: Request) -> AppState:
        return request.app.state.vibedump  # type: ignore[no-any-return]

    State = Annotated[AppState, Depends(get_state)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dumps")
    def list_dumps(
        st: State,  # type: ignore[valid-type]
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        dumps = st.db.list_dumps(limit=limit, offset=offset)
        return {"items": [_dump_to_dict(d) for d in dumps], "limit": limit, "offset": offset}

    @app.post("/api/dumps", status_code=status.HTTP_201_CREATED)
    def create_dump(
        body: CreateDumpRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be blank")
        dump_id = st.db.create_dump(title, body.metadata)
        if body.audio_path:
            transcript = st.pipeline.registry.stt["fake"].transcribe(body.audio_path)
            turn_id = st.db.add_turn(dump_id, "user", transcript, audio_path=body.audio_path)
            st.memory.remember(dump_id, "turn", turn_id, transcript)
        st.bus.publish("dump.created", {"dump_id": dump_id, "title": title})
        dump = st.db.get_dump(dump_id)
        if dump is None:
            raise HTTPException(status_code=500, detail="dump disappeared after insert")
        return _dump_to_dict(dump)

    @app.get("/api/dumps/{dump_id}")
    def get_dump(dump_id: int, st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        dump = st.db.get_dump(dump_id)
        if dump is None:
            raise HTTPException(status_code=404, detail="dump not found")
        return _dump_to_dict(dump)

    @app.delete("/api/dumps/{dump_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_dump(dump_id: int, st: State) -> None:  # type: ignore[valid-type]
        if st.db.get_dump(dump_id) is None:
            raise HTTPException(status_code=404, detail="dump not found")
        st.db.delete_dump(dump_id)
        st.bus.publish("dump.deleted", {"dump_id": dump_id})

    @app.get("/api/dumps/{dump_id}/turns")
    def list_turns(dump_id: int, st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        if st.db.get_dump(dump_id) is None:
            raise HTTPException(status_code=404, detail="dump not found")
        return {"items": [_turn_to_dict(t) for t in st.db.list_turns(dump_id)]}

    @app.get("/api/dumps/{dump_id}/blueprint")
    def get_blueprint(dump_id: int, st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        if st.db.get_dump(dump_id) is None:
            raise HTTPException(status_code=404, detail="dump not found")
        blueprint = st.db.get_latest_blueprint(dump_id)
        if blueprint is None:
            raise HTTPException(status_code=404, detail="no blueprint yet")
        return _blueprint_to_dict(blueprint)

    @app.post("/api/dumps/{dump_id}/ingest-fake", status_code=status.HTTP_201_CREATED)
    def ingest_fake(dump_id: int, st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        dump = st.db.get_dump(dump_id)
        if dump is None:
            raise HTTPException(status_code=404, detail="dump not found")
        audio_path = f"dump-{dump_id}.wav"
        result = st.pipeline.ingest_fake_into_dump(dump_id, audio_path)
        st.bus.publish(
            "blueprint.generated",
            {"dump_id": result.dump_id, "blueprint_id": st.db.get_latest_blueprint(dump_id)},
        )
        return {
            "dump_id": result.dump_id,
            "transcript": result.transcript,
            "blueprint": result.blueprint,
        }

    @app.post("/api/dumps/{dump_id}/turn", status_code=status.HTTP_201_CREATED)
    def add_turn(dump_id: int, body: AddTurnRequest, st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        if st.db.get_dump(dump_id) is None:
            raise HTTPException(status_code=404, detail="dump not found")
        st.bus.publish("dump.user_turn", {"dump_id": dump_id, "text": body.text})
        try:
            st.pipeline.add_user_turn(dump_id, body.text)
            result = st.pipeline.step_listener(dump_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "dump_id": result.dump_id,
            "action": result.action.value,
            "assistant_text": result.assistant_text,
            "status": result.status,
            "blueprint": result.blueprint,
        }

    @app.patch("/api/dumps/{dump_id}/status")
    def update_status(
        dump_id: int,
        body: UpdateStatusRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        if not st.db.update_dump_status(dump_id, body.status):
            raise HTTPException(status_code=404, detail="dump not found")
        if body.status in DeviceState.__members__.values():
            st.device_state = DeviceState(body.status)
        st.bus.publish("dump.status", {"dump_id": dump_id, "status": body.status})
        return {"dump_id": dump_id, "status": body.status}

    @app.get("/api/search")
    def search(
        st: State,  # type: ignore[valid-type]
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        results = st.memory.search(q, limit=limit)
        return {"query": q, "results": results}

    @app.get("/api/rag/memory")
    def rag_memory(
        st: State,  # type: ignore[valid-type]
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        return {"query": q, "results": st.memory.search(q, limit=limit)}

    @app.get("/api/providers")
    def providers(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        from dataclasses import asdict
        registry = st.pipeline.registry
        health = [asdict(h) for h in registry.health()]
        configs = [_provider_to_dict(c) for c in st.db.list_provider_configs()]
        return {"health": health, "configs": configs}

    @app.post("/api/providers", status_code=status.HTTP_201_CREATED)
    def upsert_provider(
        body: ProviderUpsertRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        config_id = st.db.upsert_provider_config(body.name, body.kind, body.enabled, body.config)
        st.bus.publish("provider.updated", {"name": body.name, "kind": body.kind})
        return {"id": config_id, "name": body.name, "kind": body.kind, "enabled": body.enabled}

    @app.get("/api/events")
    def events(st: State) -> StreamingResponse:  # type: ignore[valid-type]
        def stream() -> Iterator[str]:
            # Stream the current snapshot then end. Browsers reconnect via
            # EventSource; long-polling is handled on the dashboard side so the
            # server stays simple and tests can finish deterministically.
            for event in st.bus.snapshot():
                yield _format_sse(event.event_type, event.payload, event.created_at)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # M5: Achievements, profile, and mascot PNG routes
    # ------------------------------------------------------------------

    MASCOT_STATES: tuple[str, ...] = (
        "idle",
        "listening",
        "thinking",
        "speaking",
        "error",
        "level_up",
        "sleeping",
        "draft",
        "ready",
    )
    _FALLBACK_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
        b"A\x9c\x82\xc8\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    @app.get("/api/achievements")
    def list_achievements_route(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        achievements = st.db.list_achievements()
        unlocked = sum(1 for a in achievements if a.unlocked_at is not None)
        return {
            "items": [_achievement_to_dict(a) for a in achievements],
            "unlocked_count": unlocked,
            "total": len(achievements),
        }

    @app.get("/api/profile")
    def get_profile_route(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        return _profile_to_dict(st.db.get_profile())

    @app.patch("/api/profile")
    def patch_profile_route(
        body: ProfilePatchRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        if body.xp_delta is not None and body.xp_delta < 0:
            raise HTTPException(status_code=400, detail="xp_delta must be non-negative")
        if body.name is not None and not body.name.strip():
            raise HTTPException(status_code=400, detail="name cannot be blank")
        if body.name is not None:
            st.db.update_profile_name(body.name)
        if body.xp_delta is not None and body.xp_delta > 0:
            st.db.grant_xp(body.xp_delta)
        return _profile_to_dict(st.db.get_profile())

    @app.get("/api/mascot/{state}.png")
    def mascot_png_route(state: str, st: State) -> Response:  # type: ignore[valid-type]
        normalized = state.strip().lower()
        if normalized not in MASCOT_STATES:
            raise HTTPException(status_code=404, detail=f"unknown mascot state: {state}")
        frame = st.mascot_renderer.render(normalized)
        if not frame.png_bytes:
            return Response(
                content=_FALLBACK_PNG,
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "X-Mascot-Mode": "degraded",
                },
            )
        return Response(
            content=frame.png_bytes,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if not DASHBOARD_FILE.exists():
            return "<h1>💩 Vibe-Dump</h1><p>Dashboard missing.</p>"
        return DASHBOARD_FILE.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # M7: Hardware HTTP routes (Whisplay display, PiSugar telemetry,
    # push-to-talk audio capture + STT).
    # ------------------------------------------------------------------

    def _whisplay_buttons_pending() -> list[str]:
        """Drain the Whisplay's button queue; returns [] if no bridge."""
        if app_state.whisplay is None:
            return []
        try:
            pending = app_state.whisplay.read_buttons()
        except Exception:
            return []
        return sorted(pending)

    def _ptt_worker(
        job_id: str,
        dump_id: int,
        duration_s: float,
        wav_path: str,
    ) -> None:
        """Background thread body for a single push-to-talk session.

        Sequence: record -> transcribe -> add_user_turn -> publish event.
        Every error path sets ``status="error"`` and stashes the message in
        ``transcript`` so the dashboard can show *something* meaningful.
        """
        with app_state.ptt_lock:
            job = app_state.ptt_jobs.get(job_id)
        if job is None:
            return

        try:
            if app_state.audio_capture is not None:
                try:
                    app_state.audio_capture.record(duration_s, wav_path)
                except Exception as exc:
                    job.status = "error"
                    job.error = f"record failed: {exc}"
                    job.transcript = f"[record failed: {exc}]"
                    app_state.bus.publish(
                        "ptt.completed",
                        {
                            "job_id": job_id,
                            "dump_id": dump_id,
                            "wav_path": wav_path,
                            "status": "error",
                            "error": job.error,
                        },
                    )
                    return
            else:
                # No capture backend: write a silent WAV so STT still works
                # in dev / CI.
                from vibedump.integrations.audio_capture import _write_silent_wav

                _write_silent_wav(wav_path, duration_s)

            with app_state.ptt_lock:
                if job.status == "cancelled":
                    return
                job.status = "transcribing"

            provider_name = app_state.ptt_stt_provider
            try:
                stt = app_state.pipeline.registry.stt[provider_name]
            except KeyError:
                stt = next(iter(app_state.pipeline.registry.stt.values()), None)
                if stt is None:
                    raise RuntimeError("no STT provider configured")
            transcript = stt.transcribe(wav_path)

            try:
                app_state.pipeline.add_user_turn(dump_id, transcript)
            except Exception as exc:
                job.status = "error"
                job.error = f"add_user_turn failed: {exc}"
                job.transcript = transcript
                app_state.bus.publish(
                    "ptt.completed",
                    {
                        "job_id": job_id,
                        "dump_id": dump_id,
                        "wav_path": wav_path,
                        "transcript": transcript,
                        "status": "error",
                        "error": job.error,
                    },
                )
                return

            job.status = "done"
            job.transcript = transcript
            app_state.bus.publish(
                "ptt.completed",
                {
                    "job_id": job_id,
                    "dump_id": dump_id,
                    "wav_path": wav_path,
                    "transcript": transcript,
                },
            )
        except Exception as exc:
            job.status = "error"
            job.error = f"worker crashed: {exc}"
            job.transcript = job.transcript or f"[worker crashed: {exc}]"
            app_state.bus.publish(
                "ptt.completed",
                {
                    "job_id": job_id,
                    "dump_id": dump_id,
                    "wav_path": wav_path,
                    "status": "error",
                    "error": job.error,
                },
            )

    def _ptt_to_dict(job: PttJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "dump_id": job.dump_id,
            "wav_path": job.wav_path,
            "started_at": job.started_at,
            "status": job.status,
            "transcript": job.transcript,
            "error": job.error,
        }

    @app.get("/api/hardware/status")
    def hardware_status(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        pisugar_payload: dict[str, Any] | None
        if st.pisugar is None:
            pisugar_payload = None
        else:
            try:
                pisugar_payload = _battery_to_dict(st.pisugar.read())
            except Exception:
                pisugar_payload = None
        return {
            "whisplay": st.whisplay is not None,
            "pisugar": pisugar_payload,
            "audio_capture": st.audio_capture is not None,
            "buttons_pending": _whisplay_buttons_pending(),
        }

    @app.post("/api/hardware/display")
    def hardware_display(
        body: DisplayFrameRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        if st.whisplay is None:
            raise HTTPException(status_code=503, detail="whisplay not available")
        try:
            png_bytes = base64.b64decode(body.png_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}") from exc
        if not png_bytes:
            raise HTTPException(status_code=400, detail="png_base64 decoded to empty bytes")
        st.whisplay.display_frame(png_bytes)
        return {"ok": True, "bytes_sent": len(png_bytes), "width": body.width, "height": body.height}

    @app.get("/api/hardware/pisugar")
    def hardware_pisugar(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        if st.pisugar is None:
            raise HTTPException(status_code=503, detail="pisugar not available")
        reading = st.pisugar.read()
        return _battery_to_dict(reading)

    @app.post("/api/hardware/ptt/start", status_code=status.HTTP_202_ACCEPTED)
    def hardware_ptt_start(
        body: PttStartRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        if st.audio_capture is None:
            raise HTTPException(status_code=503, detail="audio capture not available")
        # We do NOT validate the dump id here on purpose: the background
        # worker is allowed to fail with ``status="error"`` if the dump
        # was deleted between the start request and the recording
        # finishing. The dashboard polls for status and surfaces the
        # error toast, so the failure is observable end-to-end.
        job_id = uuid4().hex
        PTT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        wav_path = str(PTT_TMP_DIR / f"vibedump_ptt_{job_id}.wav")
        job = PttJob(
            job_id=job_id,
            dump_id=body.dump_id,
            wav_path=wav_path,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            status="recording",
        )
        with st.ptt_lock:
            st.ptt_jobs[job_id] = job
        thread = threading.Thread(
            target=_ptt_worker,
            args=(job_id, body.dump_id, body.duration_s, wav_path),
            name=f"ptt-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"job_id": job_id, "wav_path": wav_path, "dump_id": body.dump_id}

    @app.post("/api/hardware/ptt/cancel")
    def hardware_ptt_cancel(
        body: PttCancelRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        with st.ptt_lock:
            job = st.ptt_jobs.get(body.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="ptt job not found")
        job.status = "cancelled"
        if st.audio_capture is not None:
            try:
                st.audio_capture.cancel()
            except Exception:
                pass
        return {"job_id": body.job_id, "status": "cancelled"}

    @app.get("/api/hardware/ptt/{job_id}")
    def hardware_ptt_status(
        job_id: str,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        with st.ptt_lock:
            job = st.ptt_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="ptt job not found")
        return _ptt_to_dict(job)

    return app


def _format_sse(event_type: str, payload: dict[str, Any], created_at: str) -> str:
    body = json.dumps({"event_type": event_type, "payload": payload, "created_at": created_at})
    return f"event: {event_type}\ndata: {body}\n\n"
