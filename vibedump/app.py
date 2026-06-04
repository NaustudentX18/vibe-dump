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
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from .integrations.rclone_sync import (
    HOST_LAYOUT,
    RcloneBridge,
    SyncResult,
    make_rclone,
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
    # M6 storage: rclone bridge, local data dir, and the in-memory snapshot
    # of the last export/sync timestamps + their results. The data dir is
    # only used as a parent for derived paths (exports/, audio/, etc.).
    rclone: RcloneBridge | None = None
    data_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "vibedump")
    # Tracks the most recent storage activity for the dashboard's status
    # drawer. Keyed by string attribute name; values are JSON-serialisable.
    storage_state: dict[str, Any] = field(default_factory=dict)
    # M9 agent runtime. Each field is ``None`` when its parallel module
    # has not landed; routes 503 cleanly in that case so the rest of the
    # app still works. ``agent_daemon_thread`` is the background pump
    # started by ``_create_default_state`` when the runtime is complete.
    agent_queue: Any = None
    agent: Any = None
    agent_registry: Any = None
    agent_daemon: Any = None
    agent_daemon_thread: threading.Thread | None = None
    agent_runtime_error: str | None = None

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
    # M6: rclone bridge. ``make_rclone`` already does the auto-fallback to
    # the in-memory fake when the real binary is missing, so we don't have
    # to guard with a try/except like the hardware bridges above.
    state.rclone = make_rclone("auto")
    # M9 agent runtime. Best-effort: each parallel module is imported
    # lazily; if any is missing we record the reason and the routes
    # return 503 with a clear error. The daemon thread only starts if
    # every required piece is available.
    _wire_agent_runtime(state)
    return state


def _wire_agent_runtime(state: AppState) -> None:
    """Best-effort wiring of the M9 agent runtime onto ``state``.

    The four pre-assumed modules are imported individually so a single
    missing module does not poison the others. The daemon is started
    only when all four land; otherwise ``state.agent_runtime_error``
    holds a short, human-readable reason the routes surface as 503.
    """
    errors: list[str] = []

    # JobQueue lives in this package already; the import here is for
    # symmetry with the rest of the agent runtime and to pin the
    # dependency at the wiring site.
    try:
        from .agent.queue import JobQueue as _JobQueue

        # ``Database`` exposes its connection target as ``.path``; we
        # reuse that exact path so the queue writes to the same SQLite
        # file the rest of the app uses. For in-memory databases this
        # is also ``":memory:"``, which sqlite3 understands.
        state.agent_queue = _JobQueue(state.db.path)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"queue: {exc}")
        state.agent_queue = None

    try:
        from .agent.core import OpenClaude as _OpenClaude
        from .agent.config import AgentConfig as _AgentConfig

        # ``OpenClaude`` needs (config, tools, llm). We build a default
        # config and reuse the registry we just constructed above. The
        # LLM is None so the runtime falls back to its built-in default.
        config = _AgentConfig()
        # M9.5: wire the EventBus so the agent publishes
        # ``agent.trace`` SSE events for the dashboard's trace panel.
        # Best-effort: if ``state.bus`` is None the agent still runs.
        state.agent = _OpenClaude(
            config=config,
            tools=state.agent_registry,
            bus=getattr(state, "bus", None),
        )
    except Exception as exc:
        errors.append(f"core: {exc}")
        state.agent = None

    try:
        # The parallel agent module exposes ``build_tools`` (a list of
        # ``ToolDefinition``) plus a ``ToolRegistry`` wrapper. The spec
        # referred to the combination as ``build_default_registry``;
        # we wire both halves here.
        from .agent.tools import build_tools as _build_tools
        from .agent.registry import ToolRegistry as _ToolRegistry

        tools = _build_tools(
            state.db,
            state.pipeline,
            bridge=state.rclone,
            data_dir=state.data_dir,
        )
        state.agent_registry = _ToolRegistry(tools)
    except Exception as exc:
        errors.append(f"tools: {exc}")
        state.agent_registry = None

    # Only start the daemon if every required piece is present.
    if state.agent_queue is not None and state.agent is not None:
        try:
            from .agent.daemon import OpenClaudeDaemon as _Daemon

            state.agent_daemon = _Daemon(
                queue=state.agent_queue, agent=state.agent
            )
            state.agent_daemon_thread = threading.Thread(
                target=state.agent_daemon.run_forever,
                name="agent-daemon",
                daemon=True,
            )
            state.agent_daemon_thread.start()
        except Exception as exc:
            errors.append(f"daemon: {exc}")
            state.agent_daemon = None
            state.agent_daemon_thread = None

    if errors:
        state.agent_runtime_error = "; ".join(errors)


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
        from fastapi import (
            Depends,
            FastAPI,
            File,
            Form,
            HTTPException,
            Query,
            Request,
            Response,
            UploadFile,
            status,
        )
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

    class StorageExportRequest(BaseModel):
        dest_name: str | None = Field(default=None, max_length=120)

    class StorageRedactPreviewRequest(BaseModel):
        config: dict[str, Any] = Field(default_factory=dict)

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

    # ------------------------------------------------------------------
    # M6: Storage HTTP routes (rclone sync, bundle export/import, redact
    # preview). All five routes share a single helper to publish a
    # ``storage.*`` bus event so the dashboard's SSE listener can toast on
    # completion without polling.
    # ------------------------------------------------------------------

    def _publish_storage(event_kind: str, **payload: Any) -> None:
        try:
            app_state.bus.publish(f"storage.{event_kind}", payload)
        except Exception:
            # The bus is best-effort for UI feedback; never let a publish
            # error break the storage route.
            pass

    def _storage_remote_name() -> str:
        """Return the configured remote name (env var) for display.

        Always returns the env-driven name regardless of bridge availability
        so the dashboard can show the operator what *would* be used.
        """
        from .integrations.rclone_sync import REMOTE_ENV_VAR

        return os.environ.get(REMOTE_ENV_VAR, "gdrive:")

    @app.get("/api/storage/status")
    def storage_status(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        bridge = st.rclone
        rclone_available = bool(bridge is not None and bridge.is_available())
        snapshot = dict(st.storage_state)
        return {
            "rclone_available": rclone_available,
            "remote": _storage_remote_name(),
            "host_layout": dict(HOST_LAYOUT),
            "last_export_at": snapshot.get("last_export_at"),
            "last_sync_at": snapshot.get("last_sync_at"),
            "last_sync_result": snapshot.get("last_sync_result"),
        }

    @app.post("/api/storage/sync")
    def storage_sync(st: State) -> dict[str, Any]:  # type: ignore[valid-type]
        bridge = st.rclone
        if bridge is None or not bridge.is_available():
            raise HTTPException(
                status_code=503,
                detail="rclone bridge is not available on this host",
            )
        # Sync the entire host data dir to the remote. The remote path
        # itself is the bucket root; the bridge is responsible for naming.
        host_dir = st.data_dir
        host_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = bridge.sync(host_dir, "vibedump")
        except Exception as exc:  # defensive: bridge contract may raise
            raise HTTPException(status_code=502, detail=f"sync failed: {exc}") from exc
        result_dict = {
            "ok": result.ok,
            "bytes_transferred": result.bytes_transferred,
            "files_transferred": result.files_transferred,
            "duration_seconds": result.duration_seconds,
            "error": result.error,
        }
        if not result.ok:
            # Mirror the real error from the bridge into the response body
            # so the dashboard can show it; HTTP 502 because the upstream
            # (rclone remote) is what failed.
            _publish_storage("sync_failed", error=result.error or "unknown error")
            raise HTTPException(
                status_code=502,
                detail=result.error or "sync failed",
            )
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st.storage_state["last_sync_at"] = now_iso
        st.storage_state["last_sync_result"] = result_dict
        _publish_storage("sync_ok", synced_at=now_iso, **result_dict)
        return {"ok": True, "manifest": None, "error": None}

    @app.post("/api/storage/export")
    def storage_export(
        body: StorageExportRequest,
        st: State,  # type: ignore[valid-type]
    ) -> dict[str, Any]:
        try:
            from .integrations.backup import export_bundle
        except ImportError as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=503,
                detail=f"backup integration is not available: {exc}",
            ) from exc
        dest_dir = st.data_dir / "exports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = body.dest_name.strip() if body.dest_name and body.dest_name.strip() else stamp
        # Sanitise the suffix: keep alnum/dot/dash/underscore only, fall
        # back to the timestamp when nothing valid remains.
        import re as _re

        clean = _re.sub(r"[^A-Za-z0-9._-]+", "_", suffix).strip("._-") or stamp
        dest_zip = dest_dir / f"vibedump-{clean}.zip"
        audio_dir = st.data_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        try:
            manifest = export_bundle(st.db, audio_dir, dest_zip)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"export failed: {exc}") from exc
        try:
            size_bytes = dest_zip.stat().st_size
        except OSError:
            size_bytes = 0
        from dataclasses import asdict

        manifest_dict = asdict(manifest)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st.storage_state["last_export_at"] = now_iso
        st.storage_state["last_export_path"] = str(dest_zip)
        st.storage_state["last_export_manifest"] = manifest_dict
        _publish_storage(
            "export_ok",
            path=str(dest_zip),
            manifest=manifest_dict,
            size_bytes=size_bytes,
        )
        return {
            "ok": True,
            "path": str(dest_zip),
            "manifest": manifest_dict,
            "size_bytes": size_bytes,
        }

    @app.post("/api/storage/import")
    async def storage_import(
        st: State,  # type: ignore[valid-type]
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        try:
            from .integrations.backup import BACKUP_VERSION, import_bundle
        except ImportError as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=503,
                detail=f"backup integration is not available: {exc}",
            ) from exc
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail="no file provided")
        if not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=415,
                detail=f"unsupported media type: expected .zip, got {file.filename!r}",
            )
        # Spool the upload to a temp file so import_bundle can read it
        # as a real zip path. ``delete=False`` because zipfile may
        # reopen it on some platforms.
        import tempfile as _tempfile

        tmp_fd, tmp_path = _tempfile.mkstemp(prefix="vibedump-import-", suffix=".zip")
        try:
            with os.fdopen(tmp_fd, "wb") as out:
                while True:
                    chunk = await file.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            # Peek at the manifest to surface a 422 on version mismatch
            # *before* mutating the database.
            import json as _json
            import zipfile as _zipfile

            try:
                with _zipfile.ZipFile(tmp_path, "r") as zf:
                    manifest_raw = zf.read("manifest.json").decode("utf-8")
                manifest_obj = _json.loads(manifest_raw)
            except (KeyError, _zipfile.BadZipFile, _json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid backup bundle: {exc}",
                ) from exc
            bundle_version = manifest_obj.get("version") if isinstance(manifest_obj, dict) else None
            if bundle_version != BACKUP_VERSION:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"backup version mismatch: bundle={bundle_version!r} "
                        f"host={BACKUP_VERSION!r}"
                    ),
                )
            audio_dir = st.data_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            manifest = import_bundle(st.db, audio_dir, Path(tmp_path))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"import failed: {exc}") from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        from dataclasses import asdict

        manifest_dict = asdict(manifest)
        _publish_storage("import_ok", manifest=manifest_dict)
        return {"ok": True, "manifest": manifest_dict}

    @app.post("/api/storage/redact-preview")
    def storage_redact_preview(
        body: StorageRedactPreviewRequest,
    ) -> dict[str, Any]:
        try:
            from .integrations.config_redact import redact
        except ImportError as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=503,
                detail=f"config_redact integration is not available: {exc}",
            ) from exc
        return {"redacted": redact(body.config)}

    # ------------------------------------------------------------------
    # M9: Agent HTTP routes. Five endpoints: enqueue, list, get, cancel,
    # retry, plus a health check. All six short-circuit to 503 with a
    # clear error if the parallel agent modules have not landed.
    # ------------------------------------------------------------------

    class AgentRunRequest(BaseModel):
        prompt: str = Field(min_length=1, max_length=8000)
        kind: str = Field(default="oneshot", min_length=1, max_length=40)
        priority: int = Field(default=0, ge=-100, le=100)

    def _agent_available() -> bool:
        return app_state.agent_queue is not None

    def _agent_unavailable_detail() -> str:
        if app_state.agent_runtime_error:
            return f"agent runtime unavailable: {app_state.agent_runtime_error}"
        return "agent runtime unavailable on this host"

    def _job_to_dict(record: Any) -> dict[str, Any]:
        return {
            "id": record.id,
            "kind": record.kind,
            "status": record.status,
            "priority": record.priority,
            "prompt": record.prompt,
            "result": record.result,
            "error": record.error,
            "attempts": record.attempts,
            "max_attempts": record.max_attempts,
            "created_at": record.created_at,
            "claimed_at": record.claimed_at,
            "completed_at": record.completed_at,
            "metadata": dict(record.metadata or {}),
        }

    def _queue_counts(queue: Any) -> dict[str, int]:
        """Return {pending, claimed, complete, failed, cancelled} counts."""
        out: dict[str, int] = {
            "pending": 0,
            "claimed": 0,
            "complete": 0,
            "failed": 0,
            "cancelled": 0,
        }
        # ``list_jobs`` doesn't paginate by status, so we issue one call
        # per known status. The table is small in dev; the dashboard
        # polls once per few seconds at most.
        for status_name in tuple(out):
            try:
                rows = queue.list_jobs(status=status_name, limit=10_000)
            except Exception:
                rows = []
            out[status_name] = len(rows)
        return out

    @app.post("/api/agent/run")
    def agent_run(body: AgentRunRequest) -> dict[str, Any]:
        if not _agent_available():
            raise HTTPException(status_code=503, detail=_agent_unavailable_detail())
        prompt = body.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt cannot be blank")
        record = app_state.agent_queue.enqueue(
            body.kind, prompt, priority=body.priority
        )
        app_state.bus.publish(
            "agent.job_enqueued",
            {"job_id": record.id, "kind": record.kind, "priority": record.priority},
        )
        return {"job_id": record.id, "status": record.status}

    @app.get("/api/agent/jobs")
    def agent_jobs(
        status_filter: str | None = Query(default=None, alias="status", max_length=20),
        kind: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        if not _agent_available():
            raise HTTPException(status_code=503, detail=_agent_unavailable_detail())
        items = app_state.agent_queue.list_jobs(
            status=status_filter, kind=kind, limit=limit
        )
        return {
            "items": [_job_to_dict(j) for j in items],
            "counts": _queue_counts(app_state.agent_queue),
        }

    @app.get("/api/agent/jobs/{job_id}")
    def agent_job_get(job_id: str) -> dict[str, Any]:
        if not _agent_available():
            raise HTTPException(status_code=503, detail=_agent_unavailable_detail())
        record = app_state.agent_queue.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="agent job not found")
        return _job_to_dict(record)

    @app.post("/api/agent/jobs/{job_id}/cancel")
    def agent_job_cancel(job_id: str) -> dict[str, Any]:
        if not _agent_available():
            raise HTTPException(status_code=503, detail=_agent_unavailable_detail())
        if app_state.agent_queue.get(job_id) is None:
            raise HTTPException(status_code=404, detail="agent job not found")
        # Prefer the daemon's best-effort cancel for in-flight jobs.
        if app_state.agent_daemon is not None:
            app_state.agent_daemon.cancel(job_id)
        cancelled = app_state.agent_queue.cancel(job_id)
        if not cancelled:
            # Job was already in a terminal state; treat as 409 so the
            # client knows their request was a no-op.
            raise HTTPException(status_code=409, detail="agent job is not cancellable")
        app_state.bus.publish("agent.job_cancelled", {"job_id": job_id})
        return {"job_id": job_id, "status": "cancelled"}

    @app.post("/api/agent/jobs/{job_id}/retry")
    def agent_job_retry(job_id: str) -> dict[str, Any]:
        if not _agent_available():
            raise HTTPException(status_code=503, detail=_agent_unavailable_detail())
        original = app_state.agent_queue.get(job_id)
        if original is None:
            raise HTTPException(status_code=404, detail="agent job not found")
        if original.status not in ("failed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"agent job in status {original.status!r} cannot be retried",
            )
        new_record = app_state.agent_queue.enqueue(
            original.kind,
            original.prompt,
            priority=original.priority,
            metadata={**(original.metadata or {}), "retry_of": original.id},
        )
        app_state.bus.publish(
            "agent.job_enqueued",
            {
                "job_id": new_record.id,
                "kind": new_record.kind,
                "priority": new_record.priority,
                "retry_of": original.id,
            },
        )
        return {
            "job_id": new_record.id,
            "status": new_record.status,
            "retry_of": original.id,
        }

    @app.get("/api/agent/health")
    def agent_health() -> dict[str, Any]:
        queue = app_state.agent_queue
        daemon_obj = app_state.agent_daemon
        thread = app_state.agent_daemon_thread
        daemon_alive = bool(
            thread is not None and thread.is_alive() and daemon_obj is not None
        )
        pending = 0
        claimed = 0
        if queue is not None:
            counts = _queue_counts(queue)
            pending = counts["pending"]
            claimed = counts["claimed"]
        tool_names: list[str] = []
        registry = app_state.agent_registry
        if registry is not None:
            names_getter = getattr(registry, "list", None) or getattr(
                registry, "names", None
            )
            if callable(names_getter):
                try:
                    tool_names = list(names_getter())
                except Exception:
                    tool_names = []
            elif hasattr(registry, "tools"):
                try:
                    tool_names = list(registry.tools.keys())  # type: ignore[attr-defined]
                except Exception:
                    tool_names = []
        return {
            "daemon": daemon_alive,
            "queue_pending": pending,
            "queue_claimed": claimed,
            "tools": tool_names,
            "runtime_error": app_state.agent_runtime_error,
        }

    return app

    return app


def _format_sse(event_type: str, payload: dict[str, Any], created_at: str) -> str:
    body = json.dumps({"event_type": event_type, "payload": payload, "created_at": created_at})
    return f"event: {event_type}\ndata: {body}\n\n"
