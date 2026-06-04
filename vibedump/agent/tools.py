"""OpenClaude tool implementations for the Vibe-Dump agent runtime.

Each tool wraps a slice of the existing Vibe-Dump stack (Database,
AgentPipeline, rclone bridge, backup export) behind a uniform
``ToolDefinition`` envelope. The LLM-facing surface is JSON Schema
derived from a Pydantic input model; the handlers are sync callables
that accept the validated model and return a dict.

Naming policy: identifiers MUST NOT contain the substrings
``api_key``, ``secret``, ``token``, or ``password`` (project rule).
``kind``, ``name``, ``directive``, ``value``, and ``field`` are the
preferred substitutes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict as _asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Literal, TYPE_CHECKING, TypeVar

from pydantic import BaseModel, Field

# Generic over the concrete Pydantic input model so handlers keep
# their precise type hints while the registry sees a uniform surface.
InputT = TypeVar("InputT", bound=BaseModel)

from ..agent_pipeline import AgentPipeline
from ..database import Database

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..integrations.rclone_sync import RcloneBridge

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Envelope types
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Wraps any failure raised by a tool handler.

    Carries the tool ``name`` and a human-readable ``message`` so the
    LLM layer can surface a structured error back to the model
    instead of leaking stack traces.
    """

    def __init__(self, name: str, message: str) -> None:
        super().__init__(f"{name}: {message}")
        self.name = name
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolDefinition(Generic[InputT]):
    """A single LLM-callable tool.

    Attributes
    ----------
    name:
        Unique tool name; the LLM addresses the tool by this string.
    description:
        Human-readable explanation of what the tool does. Shown in the
        function-calling prompt.
    input_model:
        Pydantic model class describing the accepted argument shape.
        JSON Schema is generated from this via ``model_json_schema()``.
    handler:
        Sync callable ``(args: InputT) -> dict``. Receives the
        validated Pydantic model instance. May raise ``ToolError``
        to surface a structured failure; any other exception is
        wrapped by the registry at dispatch time.
    """

    name: str
    description: str
    input_model: type[InputT]
    handler: Callable[[InputT], dict[str, Any]] = field(compare=False)

    def input_schema(self) -> dict[str, Any]:
        """Return the JSON Schema dict for the input model.

        Convenience accessor so callers don't have to remember the
        Pydantic API surface.
        """
        return self.input_model.model_json_schema()


# ---------------------------------------------------------------------------
# Pydantic input models
# ---------------------------------------------------------------------------


class ListDumpsInput(BaseModel):
    limit: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Maximum dumps to fetch (1-500).",
    )
    status: str | None = Field(
        default=None,
        description=(
            "Optional status filter (e.g. 'ready', 'listening'). "
            "Applied after fetch."
        ),
    )


class ReadDumpInput(BaseModel):
    dump_id: int = Field(ge=1)


class AppendTurnInput(BaseModel):
    dump_id: int = Field(ge=1)
    # text has no min_length on purpose: the handler raises a structured
    # ToolError on whitespace-only text, which the LLM layer treats as
    # a recoverable error rather than a validation failure.
    text: str
    role: Literal["user"] = "user"


class WriteDumpInput(BaseModel):
    title: str = Field(min_length=1)
    initial_text: str | None = Field(
        default=None,
        description=(
            "Optional first user turn. When provided the dump is "
            "pre-seeded with this text via add_user_turn."
        ),
    )


class RunPipelineInput(BaseModel):
    dump_id: int = Field(ge=1)
    max_steps: int = Field(default=4, ge=1, le=32)


class SearchMemoryInput(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


SyncDirection = Literal["push", "pull", "both"]


class SyncStorageInput(BaseModel):
    direction: SyncDirection = "push"


class ExportBundleInput(BaseModel):
    dest_name: str | None = Field(
        default=None,
        description=(
            "Optional zip stem (without .zip). When omitted, "
            "a UTC timestamp is used."
        ),
    )


class EmptyInput(BaseModel):
    """Marker model for tools that take no arguments."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The dump statuses the pipeline can be in. ``ready`` is a terminal
# state for the active listener loop. ``listening`` / ``thinking`` are
# transient. ``draft`` is the pre-pipeline state.
_TERMINAL_STATUSES = {"ready"}

# Sub-directory layout the rclone bridge mirrors upstream. Mirrors
# ``vibedump.integrations.rclone_sync.HOST_LAYOUT`` so the agent and
# the dashboard agree.
_DEFAULT_REMOTE_PATH = "vibedump"

# Default audio dir relative to ``data_dir`` (matches app.py).
_AUDIO_SUBDIR = "audio"

# Default export dir relative to ``data_dir`` (matches app.py).
_EXPORT_SUBDIR = "exports"


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------


def build_tools(
    db: Database,
    pipeline: AgentPipeline,
    *,
    bridge: "RcloneBridge | None" = None,
    data_dir: Path | str | None = None,
    remote_path: str = _DEFAULT_REMOTE_PATH,
) -> list[ToolDefinition]:
    """Construct the full tool catalogue wired to ``db`` and ``pipeline``.

    Parameters
    ----------
    db:
        Live :class:`Database` instance.
    pipeline:
        :class:`AgentPipeline` for the active-listener tools.
    bridge:
        Optional :class:`RcloneBridge`. When ``None`` the
        ``sync_storage`` tool is still registered but its handler
        returns a structured error pointing at the missing bridge.
    data_dir:
        Host data directory; required by ``sync_storage`` and
        ``export_bundle``. Falls back to ``./data`` when ``None``.
    remote_path:
        Remote path used by ``sync_storage``.
    """
    resolved_data_dir = Path(data_dir) if data_dir is not None else Path("data")
    return [
        _make_list_dumps(db),
        _make_read_dump(db),
        _make_append_turn(pipeline),
        _make_write_dump(pipeline),
        _make_run_pipeline(pipeline),
        _make_search_memory(db),
        _make_sync_storage(bridge, resolved_data_dir, remote_path),
        _make_export_bundle(db, resolved_data_dir),
        _make_current_profile(db),
    ]


# ---------------------------------------------------------------------------
# 1. list_dumps
# ---------------------------------------------------------------------------


def _make_list_dumps(db: Database) -> ToolDefinition:
    def handler(args: ListDumpsInput) -> dict[str, Any]:
        rows = db.list_dumps(limit=args.limit)
        slim = [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        if args.status is not None:
            slim = [d for d in slim if d["status"] == args.status]
        return {"items": slim, "count": len(slim)}

    return ToolDefinition(
        name="list_dumps",
        description=(
            "List recent vibe dumps in slim form (id, title, status, "
            "created_at). Optionally filter by status. The underlying DB "
            "method does not support a status filter natively, so any "
            "status filter is applied client-side after fetch."
        ),
        input_model=ListDumpsInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 2. read_dump
# ---------------------------------------------------------------------------


def _make_read_dump(db: Database) -> ToolDefinition:
    def handler(args: ReadDumpInput) -> dict[str, Any]:
        dump = db.get_dump(args.dump_id)
        if dump is None:
            raise ToolError(
                "read_dump",
                f"dump {args.dump_id} not found",
            )
        turns = [
            {"id": t.id, "role": t.role, "text": t.text}
            for t in db.list_turns(args.dump_id)
        ]
        blueprint = db.get_latest_blueprint(args.dump_id)
        return {
            "dump": {
                "id": dump.id,
                "title": dump.title,
                "status": dump.status,
                "created_at": dump.created_at,
                "updated_at": dump.updated_at,
                "metadata": dump.metadata,
            },
            "turns": turns,
            "blueprint": (
                {
                    "id": blueprint.id,
                    "markdown": blueprint.markdown,
                    "created_at": blueprint.created_at,
                }
                if blueprint is not None
                else None
            ),
        }

    return ToolDefinition(
        name="read_dump",
        description=(
            "Return one dump plus its turns (text only) plus the latest "
            "blueprint. Raises a structured ToolError if the dump is "
            "missing, mirroring the HTTP 404 contract."
        ),
        input_model=ReadDumpInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 3. append_turn
# ---------------------------------------------------------------------------


def _make_append_turn(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: AppendTurnInput) -> dict[str, Any]:
        if not args.text.strip():
            raise ToolError(
                "append_turn",
                "text cannot be empty",
            )
        pipeline.add_user_turn(args.dump_id, args.text)
        dump = pipeline.db.get_dump(args.dump_id)
        return {
            "dump_id": args.dump_id,
            "role": args.role,
            "status": dump.status if dump is not None else None,
        }

    return ToolDefinition(
        name="append_turn",
        description=(
            "Append a user turn to a dump. Empty text is rejected with a "
            "structured error. Only the 'user' role is supported here; "
            "the active listener writes assistant turns internally."
        ),
        input_model=AppendTurnInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 4. write_dump
# ---------------------------------------------------------------------------


def _make_write_dump(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: WriteDumpInput) -> dict[str, Any]:
        title = args.title.strip()
        if not title:
            raise ToolError("write_dump", "title cannot be empty")
        # The pipeline only accepts a transcript audio path or none;
        # we always pass metadata so the dump is tagged with its origin.
        dump_id = pipeline.start_dump(
            title,
            metadata={"origin": "openlaude"},
        )
        if args.initial_text:
            pipeline.add_user_turn(dump_id, args.initial_text)
        dump = pipeline.db.get_dump(dump_id)
        return {
            "dump_id": dump_id,
            "status": dump.status if dump is not None else None,
        }

    return ToolDefinition(
        name="write_dump",
        description=(
            "Create a new dump tagged with metadata={'origin': 'openlaude'}. "
            "Optionally pre-seed the first user turn via initial_text. "
            "Returns the new dump id and its initial status. The caller "
            "may follow up with append_turn + run_pipeline."
        ),
        input_model=WriteDumpInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 5. run_pipeline
# ---------------------------------------------------------------------------


def _make_run_pipeline(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: RunPipelineInput) -> dict[str, Any]:
        steps = 0
        last_action: str | None = None
        final_status: str | None = None
        for _ in range(args.max_steps):
            result = pipeline.step_listener(args.dump_id)
            steps += 1
            last_action = result.action.value
            final_status = result.status
            if final_status in _TERMINAL_STATUSES:
                break
        return {
            "final_status": final_status,
            "steps": steps,
            "last_action": last_action,
        }

    return ToolDefinition(
        name="run_pipeline",
        description=(
            "Drive the active-listener pipeline up to ``max_steps`` "
            "cycles, stopping early when the dump reaches a terminal "
            "status (currently 'ready'). Returns the final status, the "
            "number of steps actually run, and the last listener action."
        ),
        input_model=RunPipelineInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 6. search_memory
# ---------------------------------------------------------------------------


def _make_search_memory(db: Database) -> ToolDefinition:
    def handler(args: SearchMemoryInput) -> dict[str, Any]:
        if not args.query.strip():
            return {"items": [], "count": 0}
        rows = db.search_chunks(args.query, limit=args.limit)
        items = [
            {
                "chunk_id": int(r["chunk_id"]),
                "dump_id": int(r["dump_id"]),
                "snippet": str(r["content"])[:200],
                "score": float(r["score"]) if r.get("score") is not None else None,
            }
            for r in rows
        ]
        return {"items": items, "count": len(items)}

    return ToolDefinition(
        name="search_memory",
        description=(
            "Full-text search over the chunk FTS5 index. Returns slim "
            "rows (chunk_id, dump_id, snippet, score). Empty queries "
            "are short-circuited to an empty result set."
        ),
        input_model=SearchMemoryInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 7. sync_storage
# ---------------------------------------------------------------------------


def _make_sync_storage(
    bridge: "RcloneBridge | None",
    data_dir: Path,
    remote_path: str,
) -> ToolDefinition:
    def handler(args: SyncStorageInput) -> dict[str, Any]:
        if bridge is None or not bridge.is_available():
            return {
                "ok": False,
                "manifest": None,
                "error": "rclone bridge is not available on this host",
            }
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = bridge.sync(data_dir, remote_path)
        except Exception as exc:  # bridge contract may raise
            return {
                "ok": False,
                "manifest": None,
                "error": f"sync raised: {exc}",
            }
        manifest: dict[str, Any] = {
            "bytes_transferred": int(result.bytes_transferred),
            "files_transferred": int(result.files_transferred),
            "duration_seconds": float(result.duration_seconds),
            "direction": args.direction,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return {
            "ok": result.ok,
            "manifest": manifest,
            "error": result.error,
        }

    return ToolDefinition(
        name="sync_storage",
        description=(
            "Run the M6 rclone sync for the host data dir. Returns a "
            "manifest dict on success and a structured error when the "
            "bridge is missing or the sync failed. Direction is one of "
            "push|pull|both; the bridge currently implements push only "
            "but the parameter is accepted for forward compatibility."
        ),
        input_model=SyncStorageInput,
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 8. export_bundle
# ---------------------------------------------------------------------------


def _make_export_bundle(db: Database, data_dir: Path) -> ToolDefinition:
    def handler(args: ExportBundleInput) -> dict[str, Any]:
        # Imported lazily so the agent can start even when the backup
        # integration is unavailable in some test environments.
        from ..integrations.backup import export_bundle as _export_bundle

        dest_name = args.dest_name
        export_dir = data_dir / _EXPORT_SUBDIR
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = (
            dest_name.strip()
            if isinstance(dest_name, str) and dest_name.strip()
            else stamp
        )
        # Reuse the same sanitisation rule as the HTTP route.
        safe_suffix = "".join(c for c in suffix if c.isalnum() or c in "._-") or stamp
        dest_zip = export_dir / f"{safe_suffix}.zip"
        audio_dir = data_dir / _AUDIO_SUBDIR
        audio_dir.mkdir(parents=True, exist_ok=True)
        manifest = _export_bundle(db, audio_dir, dest_zip)
        return {
            "path": str(dest_zip),
            "size_bytes": dest_zip.stat().st_size if dest_zip.exists() else 0,
            "manifest": json.loads(json.dumps(_manifest_to_dict(manifest), default=str)),
        }

    return ToolDefinition(
        name="export_bundle",
        description=(
            "Export the database + audio files to a zip bundle. Wraps "
            "the M6 backup export with a timestamped default name. "
            "Returns the on-disk path, the file size in bytes, and the "
            "backup manifest as a dict."
        ),
        input_model=ExportBundleInput,
        handler=handler,
    )


def _manifest_to_dict(manifest: Any) -> dict[str, Any]:
    """Return a JSON-serialisable dict for any BackupManifest-like object.

    Handles plain dataclasses (incl. ``frozen=True, slots=True``), named
    tuples, and bare mappings. Falls back to ``dict(manifest)`` last.
    """
    if is_dataclass(manifest):
        return _asdict(manifest)
    if hasattr(manifest, "_asdict"):  # NamedTuple
        return dict(manifest._asdict())
    if isinstance(manifest, dict):
        return dict(manifest)
    return dict(manifest)


# ---------------------------------------------------------------------------
# 9. current_profile
# ---------------------------------------------------------------------------


def _make_current_profile(db: Database) -> ToolDefinition:
    def handler(_args: EmptyInput) -> dict[str, Any]:
        profile = db.get_profile()
        return {
            "name": profile.name,
            "xp": int(profile.xp),
            "level": int(profile.level),
            "streak_days": int(profile.streak_days),
            "xp_to_next_level": max(0, 100 - (int(profile.xp) % 100)),
        }

    return ToolDefinition(
        name="current_profile",
        description=(
            "Read the singleton profile row: name, xp, level, "
            "streak_days, and an XP-to-next-level hint. The M5 gamification "
            "pipeline updates this in place; this tool only reads."
        ),
        input_model=EmptyInput,
        handler=handler,
    )


__all__ = [
    "AppendTurnInput",
    "EmptyInput",
    "ExportBundleInput",
    "ListDumpsInput",
    "ReadDumpInput",
    "RunPipelineInput",
    "SearchMemoryInput",
    "SyncDirection",
    "SyncStorageInput",
    "ToolDefinition",
    "ToolError",
    "WriteDumpInput",
    "build_tools",
]
