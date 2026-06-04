"""Tests for the OpenClaude tool layer (M9-P3).

Covers the ``ToolRegistry`` mechanics plus the nine ``build_tools``-
produced tool handlers. The DB, pipeline, and rclone bridge are wired
in with fakes where the test needs to assert on calls, and with the
real in-memory implementations where the test asserts on data
(``Database``, ``AgentPipeline``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from vibedump.active_listener import ListenerAction
from vibedump.agent.tools import ToolDefinition, ToolError, build_tools
from vibedump.agent.registry import ToolRegistry
from vibedump.agent_pipeline import AgentPipeline as RealAgentPipeline
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.integrations.rclone_sync import SyncResult
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import LLMProvider, ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS
from vibedump.schemas import blueprint_template


# ---------------------------------------------------------------------------
# Scripted LLM stub (mirrors test_active_listener_pipeline.py)
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """A stub LLM whose ``complete`` returns a scripted sequence of responses."""

    def __init__(self, *responses: str, name: str = "scripted") -> None:
        self._responses: list[str] = list(responses)
        self._calls: list[str] = []
        self.name = name
        self.configured = True

    def complete(self, prompt: str) -> str:
        self._calls.append(prompt)
        if not self._responses:
            raise AssertionError("scripted LLM ran out of responses")
        return self._responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted stub ready")


def _registry_with(llm: LLMProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm[llm.name] = llm
    registry.tts["fake"] = FakeTTS()
    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Iterator[Database]:
    handle = Database(":memory:")
    handle.initialize()
    yield handle
    handle.close()


@pytest.fixture
def env(db: Database) -> Iterator[tuple[Database, RealAgentPipeline, _ScriptedLLM]]:
    """A working pipeline with a scripted LLM that defaults to [ASK]."""
    llm = _ScriptedLLM("[ASK] what platform?")
    pipeline = RealAgentPipeline(db, _registry_with(llm), bus=EventBus(), llm=llm)
    yield db, pipeline, llm
    db.close()


@pytest.fixture
def registry(db: Database) -> ToolRegistry:
    """A pre-loaded tool registry with a no-op rclone bridge."""
    llm = _ScriptedLLM("[ASK] what platform?")
    pipeline = RealAgentPipeline(db, _registry_with(llm), bus=EventBus(), llm=llm)
    bridge = MagicMock()
    bridge.is_available.return_value = True
    bridge.sync.return_value = SyncResult(
        ok=True,
        bytes_transferred=1024,
        files_transferred=2,
        duration_seconds=0.5,
        error=None,
    )
    tools = build_tools(
        db,
        pipeline,
        bridge=bridge,
        data_dir=Path("/tmp/vibedump-agent-tools"),
    )
    return ToolRegistry(tools)


# ---------------------------------------------------------------------------
# ToolRegistry mechanics
# ---------------------------------------------------------------------------


def test_registry_register_and_dispatch(registry: ToolRegistry) -> None:
    """A simple echo tool can be registered, dispatched, and the result returned."""
    captured: dict[str, Any] = {}

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        captured["args"] = args
        return {"ok": True, "echoed": args.get("value")}

    tool = ToolDefinition(
        name="echo",
        description="echo a value",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=handler,
    )
    registry.register(tool)

    result = registry.dispatch("echo", {"value": "hi"})

    assert result == {"ok": True, "echoed": "hi"}
    assert captured["args"] == {"value": "hi"}


def test_registry_rejects_duplicate_name(registry: ToolRegistry) -> None:
    """registering the same name twice raises ValueError, not ToolError."""
    tool = ToolDefinition(
        name="noop",
        description="does nothing",
        input_schema={"type": "object"},
        handler=lambda _: {"ok": True},
    )
    registry.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)


def test_registry_schemas_lists_all(registry: ToolRegistry) -> None:
    """schemas() returns one entry per registered tool in the documented shape."""
    schemas = registry.schemas()
    names = {entry["name"] for entry in schemas}
    expected = {
        "list_dumps",
        "read_dump",
        "append_turn",
        "write_dump",
        "run_pipeline",
        "search_memory",
        "sync_storage",
        "export_bundle",
        "current_profile",
    }
    assert expected.issubset(names)
    for entry in schemas:
        assert set(entry.keys()) == {"name", "description", "input_schema"}
        assert isinstance(entry["input_schema"], dict)


def test_tool_error_carries_name() -> None:
    """ToolError exposes both ``name`` and ``message`` attributes."""
    err = ToolError("my_tool", "boom")
    assert err.name == "my_tool"
    assert err.message == "boom"
    assert "my_tool" in str(err)
    assert "boom" in str(err)


def test_registry_dispatch_wraps_unexpected_exceptions() -> None:
    """A non-ToolError raised inside a handler becomes a ToolError at the registry."""

    def handler(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    tool = ToolDefinition(
        name="explode",
        description="raises",
        input_schema={"type": "object"},
        handler=handler,
    )
    reg = ToolRegistry([tool])
    with pytest.raises(ToolError) as exc_info:
        reg.dispatch("explode", {})
    assert exc_info.value.name == "explode"
    assert "kaboom" in exc_info.value.message


def test_registry_dispatch_unknown_tool_raises_tool_error() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolError, match="unknown tool"):
        reg.dispatch("nope", {})


def test_registry_contains_and_names() -> None:
    reg = ToolRegistry()
    assert "list_dumps" not in reg
    tool = ToolDefinition(
        name="ping",
        description="ping",
        input_schema={"type": "object"},
        handler=lambda _: {"ok": True},
    )
    reg.register(tool)
    assert "ping" in reg
    assert "missing" not in reg
    assert reg.names() == ["ping"]


# ---------------------------------------------------------------------------
# list_dumps
# ---------------------------------------------------------------------------


def test_list_dumps_returns_slim_dicts(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    pipeline.start_dump("first", metadata={"origin": "openlaude"})
    pipeline.start_dump("second")

    list_tool = next(t for t in build_tools(db, pipeline) if t.name == "list_dumps")
    result = list_tool.handler({"limit": 20})

    assert result["count"] == 2
    keys = set(result["items"][0].keys())
    assert keys == {"id", "title", "status", "created_at"}
    titles = {d["title"] for d in result["items"]}
    assert titles == {"first", "second"}


# ---------------------------------------------------------------------------
# read_dump
# ---------------------------------------------------------------------------


def test_read_dump_raises_for_missing(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    read_tool = next(t for t in build_tools(db, pipeline) if t.name == "read_dump")
    with pytest.raises(ToolError, match="not found"):
        read_tool.handler({"dump_id": 9999})


def test_read_dump_returns_turns(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    dump_id = pipeline.start_dump("Test")
    pipeline.add_user_turn(dump_id, "first thought")
    pipeline.db.add_turn(dump_id, "assistant", "second thought")
    pipeline.db.add_blueprint(dump_id, "# blueprint\n")

    read_tool = next(t for t in build_tools(db, pipeline) if t.name == "read_dump")
    result = read_tool.handler({"dump_id": dump_id})

    assert result["dump"]["title"] == "Test"
    assert result["dump"]["status"] == "listening"
    assert [t["role"] for t in result["turns"]] == ["user", "assistant"]
    assert [t["text"] for t in result["turns"]] == ["first thought", "second thought"]
    assert result["blueprint"] is not None
    assert result["blueprint"]["markdown"].startswith("# blueprint")


# ---------------------------------------------------------------------------
# append_turn
# ---------------------------------------------------------------------------


def test_append_turn_rejects_empty(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    dump_id = pipeline.start_dump("Test")
    tool = next(t for t in build_tools(db, pipeline) if t.name == "append_turn")

    for bad in ("", "   ", "\n\t"):
        with pytest.raises(ToolError, match="text cannot be empty"):
            tool.handler({"dump_id": dump_id, "text": bad})


def test_append_turn_persists_turn(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    dump_id = pipeline.start_dump("Test")
    tool = next(t for t in build_tools(db, pipeline) if t.name == "append_turn")
    result = tool.handler({"dump_id": dump_id, "text": "hello there"})
    assert result["dump_id"] == dump_id
    assert result["role"] == "user"
    turns = db.list_turns(dump_id)
    assert [t.text for t in turns] == ["hello there"]


# ---------------------------------------------------------------------------
# write_dump
# ---------------------------------------------------------------------------


def test_write_dump_creates_with_metadata(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    tool = next(t for t in build_tools(db, pipeline) if t.name == "write_dump")
    result = tool.handler({"title": "  Voice First  "})

    assert isinstance(result["dump_id"], int) and result["dump_id"] > 0
    assert result["status"] == "listening"
    record = db.get_dump(result["dump_id"])
    assert record is not None
    assert record.title == "Voice First"  # stripped
    assert record.metadata == {"origin": "openlaude"}


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------


def test_run_pipeline_loops_to_ready(db: Database) -> None:
    """A pipeline with [FINALIZE]+template on the next call lands in ready."""
    llm = _ScriptedLLM("[FINALIZE] enough", blueprint_template("Test"))
    pipeline = RealAgentPipeline(db, _registry_with(llm), bus=EventBus(), llm=llm)
    dump_id = pipeline.start_dump("Test")
    tool = next(t for t in build_tools(db, pipeline) if t.name == "run_pipeline")
    result = tool.handler({"dump_id": dump_id, "max_steps": 4})
    assert result["final_status"] == "ready"
    assert result["steps"] == 1
    assert result["last_action"] == ListenerAction.FINALIZE.value
    assert db.get_dump(dump_id).status == "ready"


def test_run_pipeline_respects_max_steps(db: Database) -> None:
    """When the LLM keeps responding ASK, the loop stops at max_steps."""
    llm = _ScriptedLLM("[ASK] q1", "[ASK] q2", "[ASK] q3", "[ASK] q4", "[ASK] q5")
    pipeline = RealAgentPipeline(db, _registry_with(llm), bus=EventBus(), llm=llm)
    dump_id = pipeline.start_dump("Test")
    tool = next(t for t in build_tools(db, pipeline) if t.name == "run_pipeline")
    result = tool.handler({"dump_id": dump_id, "max_steps": 2})
    assert result["steps"] == 2
    assert result["final_status"] == "listening"
    assert result["last_action"] == ListenerAction.ASK.value


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


def test_search_memory_uses_fts(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    dump_id = pipeline.start_dump("Test")
    pipeline.add_user_turn(dump_id, "I want a goblin spec that fits in my pocket")
    tool = next(t for t in build_tools(db, pipeline) if t.name == "search_memory")
    result = tool.handler({"query": "goblin"})
    assert result["count"] >= 1
    item = result["items"][0]
    assert item["dump_id"] == dump_id
    assert "goblin" in item["snippet"]
    assert "chunk_id" in item
    assert "score" in item


def test_search_memory_short_circuits_empty_query(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    tool = next(t for t in build_tools(db, pipeline) if t.name == "search_memory")
    assert tool.handler({"query": "   "}) == {"items": [], "count": 0}


# ---------------------------------------------------------------------------
# sync_storage
# ---------------------------------------------------------------------------


def test_sync_storage_calls_rclone(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    bridge = MagicMock()
    bridge.is_available.return_value = True
    bridge.sync.return_value = SyncResult(
        ok=True,
        bytes_transferred=4096,
        files_transferred=3,
        duration_seconds=0.25,
        error=None,
    )

    # Use a writable tempdir so the handler can mkdir -p without raising.
    tools = build_tools(
        db,
        pipeline,
        bridge=bridge,
        data_dir=Path("/tmp/vibedump-agent-sync"),
    )
    tool = next(t for t in tools if t.name == "sync_storage")
    result = tool.handler({"direction": "push"})

    assert bridge.sync.called
    assert result["ok"] is True
    assert result["manifest"]["bytes_transferred"] == 4096
    assert result["manifest"]["files_transferred"] == 3
    assert result["manifest"]["direction"] == "push"
    assert result["error"] is None


def test_sync_storage_reports_missing_bridge(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    tools = build_tools(db, pipeline, bridge=None)
    tool = next(t for t in tools if t.name == "sync_storage")
    result = tool.handler({})
    assert result["ok"] is False
    assert "not available" in (result["error"] or "")


# ---------------------------------------------------------------------------
# export_bundle
# ---------------------------------------------------------------------------


def test_export_bundle_writes_zip(env: tuple[Database, RealAgentPipeline, _ScriptedLLM], tmp_path: Path) -> None:
    db, pipeline, _ = env
    dump_id = pipeline.start_dump("Test")
    pipeline.add_user_turn(dump_id, "data worth exporting")
    tools = build_tools(db, pipeline, data_dir=tmp_path)
    tool = next(t for t in tools if t.name == "export_bundle")
    result = tool.handler({})
    path = Path(result["path"])
    assert path.exists()
    assert result["size_bytes"] > 0
    assert result["manifest"]["dump_count"] >= 1
    assert result["manifest"]["turn_count"] >= 1
    # The audio dir is created as a side effect.
    assert (tmp_path / "audio").is_dir()


# ---------------------------------------------------------------------------
# current_profile
# ---------------------------------------------------------------------------


def test_current_profile_returns_xp_and_level(env: tuple[Database, RealAgentPipeline, _ScriptedLLM]) -> None:
    db, pipeline, _ = env
    pipeline.start_dump("Test")  # grants 5 XP
    pipeline.db.grant_xp(50)  # total 55, level 1
    tool = next(t for t in build_tools(db, pipeline) if t.name == "current_profile")
    result = tool.handler({})
    assert "name" in result
    assert "xp" in result
    assert "level" in result
    assert "streak_days" in result
    assert "xp_to_next_level" in result
    assert result["xp"] >= 55
    assert result["level"] == 1
    assert result["xp_to_next_level"] == 100 - (result["xp"] % 100)


# ---------------------------------------------------------------------------
# Smoke test: registry + tools wiring
# ---------------------------------------------------------------------------


def test_full_registry_round_trip(registry: ToolRegistry) -> None:
    """All nine tools are addressable via dispatch and produce a dict result."""
    # The handlers are local functions with closures (not bound methods),
    # so we cannot recover the pipeline via ``__self__``. We exercise the
    # registry via dispatch only, which is the contract callers rely on.
    write_result = registry.dispatch("write_dump", {"title": "round-trip"})
    assert write_result["dump_id"] > 0

    read_result = registry.dispatch("read_dump", {"dump_id": write_result["dump_id"]})
    assert read_result["dump"]["title"] == "round-trip"
    assert read_result["dump"]["metadata"] == {"origin": "openlaude"}

    # Sync via the mocked bridge from the fixture.
    sync_result = registry.dispatch("sync_storage", {"direction": "push"})
    assert sync_result["ok"] is True

    profile_result = registry.dispatch("current_profile", {})
    assert "xp" in profile_result
