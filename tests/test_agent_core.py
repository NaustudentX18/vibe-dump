"""Tests for the openlaude agent core (vibedump.agent)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from pydantic import BaseModel

from vibedump.agent import AgentConfig, OpenClaude
from vibedump.agent.core import (
    AgentResult,
    ChatResponse,
    Step,
    Tool,
    ToolCall,
    ToolRegistry,
)


class _EchoInput(BaseModel):
    """Module-level Pydantic model so pydantic-ai can resolve the type hint.

    Tests that need an inline model for the inline path can keep using
    nested classes, but the pydantic-ai backend evaluates ``get_type_hints``
    at function-registration time and requires a fully-qualified name.
    """

    value: str


class FakeLLMProvider:
    """Records every chat() call and returns scripted responses.

    `responses` is a list of either ChatResponse objects or callables
    `(messages, tools) -> ChatResponse`. When the list is exhausted a
    final "no more responses" message is returned so the agent never
    crashes in tests that under-provision.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.name = "fake_chat"

    def chat(self, messages, *, tools=None):
        self.calls.append({
            "messages": [dict(m) for m in messages],
            "tools": [dict(t) for t in (tools or [])],
        })
        if not self.responses:
            return ChatResponse(content="(fake LLM ran out of responses)")
        nxt = self.responses.pop(0)
        if callable(nxt):
            return nxt(messages, tools)
        return nxt


def _echo_tool():
    return Tool(
        name="echo",
        description="Echo a string back as a dict.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        fn=lambda value: {"echoed": value},
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_agent_config_defaults():
    cfg = AgentConfig()
    assert cfg.model == "qwen3-14b-agent"
    assert cfg.base_url == "http://desktop-ujsii52.local:11434"
    assert cfg.max_steps == 12
    assert cfg.temperature == 0.2
    assert "openlaude" in cfg.system_prompt
    assert "tool" in cfg.system_prompt.lower()


def test_agent_config_is_frozen():
    cfg = AgentConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.max_steps = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LLM resolution + simple message
# ---------------------------------------------------------------------------


def test_openclaude_resolves_llm_from_registry(monkeypatch):
    fake = FakeLLMProvider([ChatResponse(content="hi from registry")])
    fake_registry = type("R", (), {"llm": {"local_pc": fake}})()

    def fake_build_registry(*, include_local_pc=True):
        assert include_local_pc is True
        return fake_registry

    monkeypatch.setattr("vibedump.agent.core.build_registry", fake_build_registry)
    agent = OpenClaude(AgentConfig(), ToolRegistry())
    assert agent.llm is fake
    result = agent.run("hello")
    assert result.final_message == "hi from registry"
    assert len(fake.calls) == 1


def test_openclaude_runs_simple_message():
    fake = FakeLLMProvider([ChatResponse(content="all good")])
    agent = OpenClaude(AgentConfig(), ToolRegistry(), llm=fake)
    result = agent.run("hello")
    assert isinstance(result, AgentResult)
    assert result.final_message == "all good"
    assert result.steps == 1
    assert result.tool_calls == []


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def test_openclaude_dispatches_tool_call():
    fake = FakeLLMProvider([
        ChatResponse(
            content="I'll echo ping",
            tool_call=ToolCall(name="echo", arguments={"value": "ping"}),
        ),
        ChatResponse(content="echoed"),
    ])
    registry = ToolRegistry()
    registry.register(_echo_tool())
    agent = OpenClaude(AgentConfig(), registry, llm=fake)
    result = agent.run("run echo")
    assert result.tool_calls == [{"name": "echo", "arguments": {"value": "ping"}}]
    assert result.steps == 2
    assert result.final_message == "echoed"
    # The second chat call must have a tool message in its history.
    last_messages = fake.calls[1]["messages"]
    tool_msgs = [m for m in last_messages if m.get("role") == "tool"]
    assert tool_msgs, "expected a tool message in the second LLM call"
    assert "ping" in tool_msgs[0]["content"]


def test_openclaude_loops_until_final():
    fake = FakeLLMProvider([
        ChatResponse(content="", tool_call=ToolCall(name="echo", arguments={"value": "1"})),
        ChatResponse(content="", tool_call=ToolCall(name="echo", arguments={"value": "2"})),
        ChatResponse(content="finished"),
    ])
    registry = ToolRegistry()
    registry.register(_echo_tool())
    agent = OpenClaude(AgentConfig(), registry, llm=fake)
    result = agent.run("loop please")
    assert result.final_message == "finished"
    assert result.steps == 3
    assert len(result.tool_calls) == 2


def test_openclaude_respects_max_steps():
    def always_tool(messages, tools):
        return ChatResponse(
            content="",
            tool_call=ToolCall(name="echo", arguments={"value": "x"}),
        )

    fake = FakeLLMProvider([always_tool, always_tool, always_tool, always_tool])
    registry = ToolRegistry()
    registry.register(_echo_tool())
    agent = OpenClaude(AgentConfig(max_steps=2), registry, llm=fake)
    result = agent.run("loop forever")
    assert result.steps == 2
    assert len(result.tool_calls) == 2
    assert "max steps" in result.final_message.lower()


# ---------------------------------------------------------------------------
# Steps + result shape
# ---------------------------------------------------------------------------


def test_openclaude_emits_steps():
    seen: list[Step] = []
    fake = FakeLLMProvider([
        ChatResponse(content="", tool_call=ToolCall(name="echo", arguments={"value": "y"})),
        ChatResponse(content="summary"),
    ])
    registry = ToolRegistry()
    registry.register(_echo_tool())
    agent = OpenClaude(AgentConfig(), registry, llm=fake)
    result = agent.run("go", on_step=seen.append)
    kinds = [s.kind for s in seen]
    assert "llm" in kinds
    assert "tool" in kinds
    assert "final" in kinds
    final_steps = [s for s in seen if s.kind == "final"]
    assert final_steps
    assert final_steps[-1].content == result.final_message


def test_agent_result_includes_duration():
    fake = FakeLLMProvider([ChatResponse(content="ok")])
    agent = OpenClaude(AgentConfig(), ToolRegistry(), llm=fake)
    result = agent.run("hi")
    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def test_fallback_when_pydantic_deep_agents_missing(monkeypatch):
    # Pretend pydantic_deep_agents was never importable. The agent's
    # inline loop is the canonical implementation, so the constructor
    # and `run` must still succeed end-to-end.
    import vibedump.agent.core as core

    monkeypatch.setattr(core, "_HAS_PYDANTIC_DEEP_AGENTS", False)
    monkeypatch.setattr(core, "_PDAAgent", None)
    assert core._HAS_PYDANTIC_DEEP_AGENTS is False

    fake = FakeLLMProvider([ChatResponse(content="inline works")])
    agent = OpenClaude(AgentConfig(), ToolRegistry(), llm=fake)
    result = agent.run("ping")
    assert result.final_message == "inline works"
    assert result.steps == 1


# ---------------------------------------------------------------------------
# M9.5: Pydantic AI backend selection
# ---------------------------------------------------------------------------


def test_pydantic_ai_path_taken_when_available(monkeypatch):
    """PydanticAIBackend is constructable when _HAS_PYDANTIC_AI is True."""
    from vibedump.agent.core import PydanticAIBackend
    from vibedump.agent.tools import ToolDefinition

    import vibedump.agent.core as core

    if not core._HAS_PYDANTIC_AI:
        pytest.skip("pydantic-ai not installed in this environment")

    captured: list[str] = []

    def handler(args: _EchoInput) -> dict[str, Any]:
        captured.append(args.value)
        return {"echoed": args.value}

    tool = ToolDefinition(
        name="echo",
        description="echo",
        input_model=_EchoInput,
        handler=handler,
    )
    backend = PydanticAIBackend(AgentConfig(), [tool])
    assert backend is not None
    # Confirm the underlying pydantic_ai.Agent registered the tool.
    assert hasattr(backend, "_agent")


def test_fallback_to_inline_when_pydantic_ai_missing(monkeypatch):
    """When _HAS_PYDANTIC_AI is False, PydanticAIBackend refuses to construct."""
    from vibedump.agent.core import PydanticAIBackend
    from vibedump.agent.tools import EmptyInput, ToolDefinition

    import vibedump.agent.core as core

    monkeypatch.setattr(core, "_HAS_PYDANTIC_AI", False)
    monkeypatch.setattr(core, "_PAIAgent", None)
    monkeypatch.setattr(core, "_OpenAIChatModel", None)
    monkeypatch.setattr(core, "_OpenAIProvider", None)

    tool = ToolDefinition(
        name="noop",
        description="noop",
        input_model=EmptyInput,
        handler=lambda _: {"ok": True},
    )
    with pytest.raises(RuntimeError, match="pydantic-ai is not installed"):
        PydanticAIBackend(AgentConfig(), [tool])


def test_inline_loop_still_works_with_pydantic_ai_installed():
    """Even with pydantic-ai installed, the inline path stays the default."""
    fake = FakeLLMProvider([ChatResponse(content="still inline")])
    agent = OpenClaude(AgentConfig(), ToolRegistry(), llm=fake)
    result = agent.run("hi")
    assert result.final_message == "still inline"
    assert result.steps == 1
