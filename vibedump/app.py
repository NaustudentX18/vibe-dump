"""App factory for the Vibe-Dump web dashboard.

Builds a FastAPI app with:
- A single shared in-process state (DB, event bus, fake pipeline).
- Mobile-first dashboard served at ``/``.
- JSON API for dumps, search, providers, and SSE for live updates.

FastAPI is an optional dependency. The factory raises a clear error if it is
not installed so the package still imports cleanly for the database / RAG tests.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Iterator

from .agent_pipeline import FakeAgentPipeline
from .database import (
    BlueprintRecord,
    Database,
    DumpRecord,
    ProviderConfigRecord,
    TurnRecord,
)
from .events import EventBus
from .providers import build_registry, fake_registry
from .ragmemory import RagMemory
from .state import DeviceState

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"

DEFAULT_API_PREFIX = "/api"


@dataclass(slots=True)
class AppState:
    """Shared singletons for a Vibe-Dump web process."""

    db: Database
    bus: EventBus
    pipeline: FakeAgentPipeline
    memory: RagMemory
    device_state: DeviceState = DeviceState.IDLE

    def close(self) -> None:
        self.db.close()


def _create_default_state() -> AppState:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    # VIBEDUMP_REGISTRY=fake (default) keeps tests + zero-config dev hermetic.
    # VIBEDUMP_REGISTRY=real wires the production provider set (build_registry).
    registry_kind = os.environ.get("VIBEDUMP_REGISTRY", "fake").lower()
    registry = build_registry() if registry_kind == "real" else fake_registry()
    return AppState(
        db=db,
        bus=bus,
        pipeline=FakeAgentPipeline(db, registry=registry),
        memory=RagMemory(db),
    )


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


def create_app(state: AppState | None = None) -> Any:
    """Create the FastAPI app, wiring routes to the provided state."""
    try:
        from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
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

    class UpdateStatusRequest(BaseModel):
        status: str = Field(min_length=1, max_length=40)

    class ProviderUpsertRequest(BaseModel):
        name: str = Field(min_length=1, max_length=80)
        kind: str = Field(min_length=1, max_length=20)
        enabled: bool = True
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

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if not DASHBOARD_FILE.exists():
            return "<h1>💩 Vibe-Dump</h1><p>Dashboard missing.</p>"
        return DASHBOARD_FILE.read_text(encoding="utf-8")

    return app


def _format_sse(event_type: str, payload: dict[str, Any], created_at: str) -> str:
    body = json.dumps({"event_type": event_type, "payload": payload, "created_at": created_at})
    return f"event: {event_type}\ndata: {body}\n\n"
