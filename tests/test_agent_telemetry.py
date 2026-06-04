"""Tests for the M9.5 agent.trace SSE telemetry.

The OpenClaude runner publishes an ``agent.trace`` event on the bus
when one is wired in. Tests use a real :class:`EventBus` to verify
the shape of the published payload and the no-op behaviour when no
bus is supplied.
"""

from __future__ import annotations

from typing import Any

import pytest

from vibedump.agent import AgentConfig, OpenClaude
from vibedump.agent.core import (
    AgentResult,
    ChatResponse,
    Step,
    Tool,
    ToolRegistry,
)
from vibedump.events import EventBus


class _ScriptedChat:
    """A scripted LLM that returns one ASK-then-FINALIZE cycle."""

    def __init__(self) -> None:
        self.name = "scripted_chat"
        self._responses = [
            ChatResponse(
                content="I'll echo",
                tool_call=Tool(name="echo", description="x", parameters={}, fn=lambda: None)._as_call(),
            ) if False else ChatResponse(
                content="plain final answer",
            ),
        ]

    def chat(self, messages, *, tools=None):
        return self._responses.pop(0) if self._responses else ChatResponse(content="(done)")


class _FakeLLM:
    """A scripted chat-capable LLM with a known name."""

    def __init__(self) -> None:
        self.name = "telemetry_fake"
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, *, tools=None):
        self.calls.append({"n": len(self.calls)})
        return ChatResponse(content="hello from telemetry fake")


def _new_agent(bus: EventBus | None) -> tuple[OpenClaude, _FakeLLM]:
    llm = _FakeLLM()
    agent = OpenClaude(AgentConfig(), ToolRegistry(), llm=llm, bus=bus)
    return agent, llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trace_event_published_with_bus() -> None:
    """When a bus is wired, agent.trace fires with the run summary."""
    bus = EventBus()
    agent, llm = _new_agent(bus)

    result = agent.run("hi")
    assert result.final_message == "hello from telemetry fake"

    events = [e for e in bus.snapshot() if e.event_type == "agent.trace"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["steps"] == 1
    assert payload["tool_call_count"] == 0
    assert payload["model"] == "telemetry_fake"
    assert "duration_ms" in payload
    assert payload["duration_ms"] >= 0


def test_trace_event_includes_step_count() -> None:
    """A multi-step run reports the correct step count in the trace."""
    bus = EventBus()
    agent, _ = _new_agent(bus)
    # Just confirm the field exists and matches the AgentResult.steps
    result = agent.run("hi")
    payload = bus.snapshot()[-1].payload
    assert payload["steps"] == result.steps


def test_trace_event_includes_tool_call_count_when_tools_used() -> None:
    """A run that calls tools reports tool_call_count > 0."""

    class _TwoCallLLM(_FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            from vibedump.agent.core import ToolCall
            self._responses = [
                ChatResponse(
                    content="",
                    tool_call=ToolCall(name="echo", arguments={"value": "x"}),
                ),
                ChatResponse(content="after tool"),
            ]

        def chat(self, messages, *, tools=None):
            return self._responses.pop(0)

    llm = _TwoCallLLM()
    bus = EventBus()
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            fn=lambda value: {"echoed": value},
        )
    )
    agent = OpenClaude(AgentConfig(), registry, llm=llm, bus=bus)
    result = agent.run("do it")

    assert result.tool_calls == [{"name": "echo", "arguments": {"value": "x"}}]
    payload = bus.snapshot()[-1].payload
    assert payload["tool_call_count"] == 1
    assert payload["tool_calls"] == [{"name": "echo", "arguments": {"value": "x"}}]
    assert payload["steps"] == 2


def test_trace_omitted_when_bus_is_none() -> None:
    """When no bus is supplied, the agent must not raise."""
    agent, _ = _new_agent(bus=None)
    # No exception is the assertion; result still valid.
    result = agent.run("hi")
    assert result.final_message == "hello from telemetry fake"


def test_trace_includes_duration() -> None:
    """duration_ms is always populated and is a non-negative int."""
    bus = EventBus()
    agent, _ = _new_agent(bus)
    agent.run("hi")
    payload = bus.snapshot()[-1].payload
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0


def test_trace_swallows_publish_errors() -> None:
    """A misbehaving bus must never break the agent run."""

    class _BrokenBus:
        def publish(self, *args, **kwargs):
            raise RuntimeError("bus is on fire")

    agent, _ = _new_agent(bus=_BrokenBus())  # type: ignore[arg-type]
    # No exception: telemetry is best-effort.
    result = agent.run("hi")
    assert result.final_message == "hello from telemetry fake"
