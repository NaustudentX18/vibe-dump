"""OpenClaude tool implementations for the Vibe-Dump agent runtime.

Each tool wraps a slice of the existing Vibe-Dump stack (Database,
AgentPipeline, rclone bridge, backup export) behind a uniform
``ToolDefinition`` envelope. The LLM-facing surface is JSON schema;
the handlers are sync callables that raise ``ToolError`` on failure.

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
from typing import Any, Callable, Literal, TYPE_CHECKING

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
class ToolDefinition:
    """A single LLM-callable tool.

    Attributes
    ----------
    name:
        Unique tool name; the LLM addresses the tool by this string.
    description:
        Human-readable explanation of what the tool does. Shown in the
        function-calling prompt.
    input_schema:
        JSON Schema dict describing the accepted argument shape.
    handler:
        Sync callable ``(args: dict) -> dict``. May raise ``ToolError``
        to surface a structured failure; any other exception is wrapped
        by the registry at dispatch time.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]] = field(compare=False)


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
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 20))
        if limit <= 0 or limit > 500:
            return {"error": "limit must be between 1 and 500"}
        status_filter = args.get("status")
        rows = db.list_dumps(limit=limit)
        slim = [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        if status_filter is not None:
            slim = [d for d in slim if d["status"] == status_filter]
        return {"items": slim, "count": len(slim)}

    return ToolDefinition(
        name="list_dumps",
        description=(
            "List recent vibe dumps in slim form (id, title, status, "
            "created_at). Optionally filter by status. The underlying DB "
            "method does not support a status filter natively, so any "
            "status filter is applied client-side after fetch."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 20,
                    "description": "Maximum dumps to fetch (1-500).",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Optional status filter (e.g. 'ready', 'listening'). "
                        "Applied after fetch."
                    ),
                },
            },
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 2. read_dump
# ---------------------------------------------------------------------------


def _make_read_dump(db: Database) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        dump_id = int(args["dump_id"])
        dump = db.get_dump(dump_id)
        if dump is None:
            raise ToolError(
                "read_dump",
                f"dump {dump_id} not found",
            )
        turns = [
            {"id": t.id, "role": t.role, "text": t.text}
            for t in db.list_turns(dump_id)
        ]
        blueprint = db.get_latest_blueprint(dump_id)
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
        input_schema={
            "type": "object",
            "properties": {
                "dump_id": {"type": "integer", "minimum": 1},
            },
            "required": ["dump_id"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 3. append_turn
# ---------------------------------------------------------------------------


def _make_append_turn(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        dump_id = int(args["dump_id"])
        text = args.get("text", "")
        if not isinstance(text, str) or not text.strip():
            raise ToolError(
                "append_turn",
                "text cannot be empty",
            )
        role = args.get("role", "user")
        if role != "user":
            # The pipeline only accepts user turns via add_user_turn.
            raise ToolError(
                "append_turn",
                f"role must be 'user' (got {role!r})",
            )
        pipeline.add_user_turn(dump_id, text)
        dump = pipeline.db.get_dump(dump_id)
        return {
            "dump_id": dump_id,
            "role": role,
            "status": dump.status if dump is not None else None,
        }

    return ToolDefinition(
        name="append_turn",
        description=(
            "Append a user turn to a dump. Empty text is rejected with a "
            "structured error. Only the 'user' role is supported here; "
            "the active listener writes assistant turns internally."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "dump_id": {"type": "integer", "minimum": 1},
                "text": {"type": "string", "minLength": 1},
                "role": {
                    "type": "string",
                    "enum": ["user"],
                    "default": "user",
                },
            },
            "required": ["dump_id", "text"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 4. write_dump
# ---------------------------------------------------------------------------


def _make_write_dump(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        title = args.get("title", "").strip() if isinstance(args.get("title"), str) else ""
        if not title:
            raise ToolError("write_dump", "title cannot be empty")
        # The pipeline only accepts a transcript audio path or none;
        # we always pass metadata so the dump is tagged with its origin.
        dump_id = pipeline.start_dump(
            title,
            metadata={"origin": "openlaude"},
        )
        dump = pipeline.db.get_dump(dump_id)
        return {
            "dump_id": dump_id,
            "status": dump.status if dump is not None else None,
        }

    return ToolDefinition(
        name="write_dump",
        description=(
            "Create a new dump tagged with metadata={'origin': 'openlaude'}. "
            "Returns the new dump id and its initial status. The first "
            "turn is NOT auto-added; the caller is expected to follow up "
            "with append_turn + run_pipeline."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "initial_text": {
                    "type": "string",
                    "description": (
                        "Optional first user turn. When provided the dump "
                        "is pre-seeded with this text via add_user_turn."
                    ),
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 5. run_pipeline
# ---------------------------------------------------------------------------


def _make_run_pipeline(pipeline: AgentPipeline) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        dump_id = int(args["dump_id"])
        max_steps = int(args.get("max_steps", 4))
        if max_steps <= 0:
            return {"final_status": None, "steps": 0, "last_action": None}
        steps = 0
        last_action: str | None = None
        final_status: str | None = None
        for _ in range(max_steps):
            result = pipeline.step_listener(dump_id)
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
        input_schema={
            "type": "object",
            "properties": {
                "dump_id": {"type": "integer", "minimum": 1},
                "max_steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 32,
                    "default": 4,
                },
            },
            "required": ["dump_id"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 6. search_memory
# ---------------------------------------------------------------------------


def _make_search_memory(db: Database) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return {"items": [], "count": 0}
        limit = int(args.get("limit", 5))
        if limit <= 0 or limit > 50:
            limit = 5
        rows = db.search_chunks(query, limit=limit)
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
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 7. sync_storage
# ---------------------------------------------------------------------------


SyncDirection = Literal["push", "pull", "both"]


def _make_sync_storage(
    bridge: "RcloneBridge | None",
    data_dir: Path,
    remote_path: str,
) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        direction: SyncDirection = args.get("direction", "push")
        if direction not in ("push", "pull", "both"):
            raise ToolError(
                "sync_storage",
                f"direction must be push|pull|both (got {direction!r})",
            )
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
            "direction": direction,
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
        input_schema={
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["push", "pull", "both"],
                    "default": "push",
                },
            },
            "additionalProperties": False,
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# 8. export_bundle
# ---------------------------------------------------------------------------


def _make_export_bundle(db: Database, data_dir: Path) -> ToolDefinition:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        # Imported lazily so the agent can start even when the backup
        # integration is unavailable in some test environments.
        from ..integrations.backup import export_bundle as _export_bundle

        dest_name = args.get("dest_name")
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
        input_schema={
            "type": "object",
            "properties": {
                "dest_name": {
                    "type": "string",
                    "description": (
                        "Optional zip stem (without .zip). When omitted, "
                        "a UTC timestamp is used."
                    ),
                },
            },
            "additionalProperties": False,
        },
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
    def handler(args: dict[str, Any]) -> dict[str, Any]:
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
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
    )


__all__ = [
    "SyncDirection",
    "ToolDefinition",
    "ToolError",
    "build_tools",
]
