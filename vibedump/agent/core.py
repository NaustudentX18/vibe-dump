"""The openlaude agent core.

This module is the runtime for Vibe-Dump's `openlaude` agent. It depends
only on the existing `vibedump.providers` registry and the standard
library. Pydantic-Deep-Agents is imported best-effort: when it is
installed we record the fact but the inline tool-use loop is the
canonical implementation, so the agent works either way.

Public surface:
    OpenClaude        - the agent runner
    AgentConfig       - re-exported from .config for convenience
    AgentResult       - final outcome of `run`
    Step              - per-iteration event yielded by `run_streaming`
                         or passed to the `on_step` callback
    Tool, ToolRegistry - registration and dispatch of callable tools
    ToolCall          - one tool invocation requested by the LLM
    ChatResponse      - one LLM turn's reply
    Message           - the agent's internal message record
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Literal, Protocol

from vibedump.providers import build_registry

from .config import AgentConfig

# pydantic-deep-agents: best-effort import. We do not depend on it at
# runtime; when present we record the fact, but the inline tool-use
# loop is the canonical implementation and works without it.
try:  # pragma: no cover - presence is environment-dependent
    from pydantic_deep_agents import Agent as _PDAAgent  # type: ignore[import-not-found]
    _HAS_PYDANTIC_DEEP_AGENTS = True
except ImportError:  # pragma: no cover - exercised via monkeypatch
    _PDAAgent = None  # type: ignore[assignment]
    _HAS_PYDANTIC_DEEP_AGENTS = False


# ---------------------------------------------------------------------------
# Wire types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    tool_call: ToolCall | None = None


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_call: ToolCall | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name is not None:
            d["name"] = self.name
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_call is not None:
            tc: dict[str, Any] = {
                "name": self.tool_call.name,
                "arguments": self.tool_call.arguments,
            }
            if self.tool_call.id is not None:
                tc["id"] = self.tool_call.id
            d["tool_call"] = tc
        return d


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


class ToolRegistry:
    """Holds callable tools and exposes their schemas to the LLM."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-style function-calling schemas for the registered tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in (self._tools[n] for n in sorted(self._tools))
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"unknown tool {name!r}")
        return self._tools[name].fn(**arguments)


# ---------------------------------------------------------------------------
# Loop output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    index: int
    kind: Literal["llm", "tool", "final"]
    content: str
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    final_message: str
    steps: int
    tool_calls: list[dict[str, Any]]
    duration_seconds: float


# ---------------------------------------------------------------------------
# LLM adapter
# ---------------------------------------------------------------------------


class ChatCapable(Protocol):
    name: str
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse: ...


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _normalize_tool_result(value: Any) -> tuple[dict[str, Any], str]:
    """Coerce a tool return into (dict_for_step, str_for_message_history)."""
    if isinstance(value, dict):
        return value, _stringify(value)
    return {"value": value}, _stringify(value)


def _adapt_complete_only(llm: Any) -> ChatCapable:
    """Wrap a `complete(prompt) -> str` LLM as a chat-capable LLM.

    The wrapper flattens the conversation to a single prompt and returns
    the text as a plain assistant message. This is a degraded path —
    tool calls are not invoked server-side — but it lets the agent run
    end-to-end against any existing provider.
    """
    class _Shim:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.name = getattr(inner, "name", "shim")

        def chat(self, messages, *, tools=None):
            lines = [
                f"{m.get('role', 'user').upper()}: {m.get('content', '')}"
                for m in messages
            ]
            return ChatResponse(
                content=self._inner.complete("\n".join(lines)),
                tool_call=None,
            )

    return _Shim(llm)


def _resolve_default_llm() -> ChatCapable:
    """Build a registry and return the local_pc entry (wrapped if needed)."""
    registry = build_registry(include_local_pc=True)
    if "local_pc" in registry.llm:
        llm = registry.llm["local_pc"]
    else:
        llm = next(iter(registry.llm.values()), None)
    if llm is None:
        raise RuntimeError("no LLM provider is available in the registry")
    if not hasattr(llm, "chat"):
        llm = _adapt_complete_only(llm)
    return llm


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


_MAX_STEPS_REACHED_MESSAGE = "(max steps reached without a final message)"


class OpenClaude:
    """The openlaude agent core.

    Uses pydantic-deep-agents when it is importable; otherwise runs an
    inline tool-use loop in pure Python against any chat-capable LLM.
    The class API is the same in both modes.
    """

    def __init__(
        self,
        config: AgentConfig,
        tools: ToolRegistry,
        llm: Any | None = None,
    ) -> None:
        self.config = config
        self.tools = tools
        if llm is None:
            self.llm: ChatCapable = _resolve_default_llm()
        else:
            self.llm = llm if hasattr(llm, "chat") else _adapt_complete_only(llm)

    # -- prompt + message helpers ----------------------------------------

    def _build_system_prompt(self) -> str:
        tool_names = self.tools.names()
        if tool_names:
            tool_block = "Available tools:\n" + "\n".join(f"- {n}" for n in tool_names)
        else:
            tool_block = "Available tools: (none registered)"
        return f"{self.config.system_prompt}\n\n{tool_block}\n"

    def _initial_messages(self, user_message: str) -> list[Message]:
        return [
            Message(role="system", content=self._build_system_prompt()),
            Message(role="user", content=user_message),
        ]

    @staticmethod
    def _to_llm(messages: list[Message]) -> list[dict[str, Any]]:
        return [m.to_dict() for m in messages]

    # -- core loop --------------------------------------------------------

    def _drive(
        self,
        user_message: str,
        on_step: Callable[[Step], None] | None,
    ) -> tuple[str, int, list[dict[str, Any]], float]:
        messages = self._initial_messages(user_message)
        tool_schemas = self.tools.schemas()
        started = time.perf_counter()
        steps = 0
        tool_calls: list[dict[str, Any]] = []
        final = ""

        for step_index in range(self.config.max_steps):
            response = self.llm.chat(self._to_llm(messages), tools=tool_schemas)
            steps += 1
            if response.tool_call is None:
                final = response.content
                if on_step is not None:
                    on_step(Step(index=step_index, kind="final", content=final))
                break

            tc = response.tool_call
            tool_calls.append({"name": tc.name, "arguments": tc.arguments})
            messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_call=tc,
                )
            )
            if on_step is not None:
                on_step(Step(
                    index=step_index,
                    kind="llm",
                    content=response.content or "",
                    tool_name=tc.name,
                    tool_result=None,
                ))

            try:
                raw_result = self.tools.dispatch(tc.name, tc.arguments)
            except Exception as exc:  # noqa: BLE001 - tool errors become tool messages
                raw_result = {"error": f"{type(exc).__name__}: {exc}"}
            result_dict, result_str = _normalize_tool_result(raw_result)
            messages.append(Message(role="tool", name=tc.name, content=result_str))
            if on_step is not None:
                on_step(Step(
                    index=step_index,
                    kind="tool",
                    content=result_str,
                    tool_name=tc.name,
                    tool_result=result_dict,
                ))
        else:
            if not final:
                final = _MAX_STEPS_REACHED_MESSAGE
                if on_step is not None:
                    on_step(Step(
                        index=self.config.max_steps,
                        kind="final",
                        content=final,
                    ))

        return final, steps, tool_calls, time.perf_counter() - started

    # -- public API -------------------------------------------------------

    def run(
        self,
        user_message: str,
        *,
        on_step: Callable[[Step], None] | None = None,
    ) -> AgentResult:
        final, steps, tool_calls, duration = self._drive(user_message, on_step)
        return AgentResult(
            final_message=final,
            steps=steps,
            tool_calls=tool_calls,
            duration_seconds=duration,
        )

    def run_streaming(
        self,
        user_message: str,
        *,
        on_step: Callable[[Step], None] | None = None,
    ) -> Iterator[Step]:
        """Yield each Step as the agent runs.

        The companion `run()` returns the final `AgentResult`. This
        iterator yields the same Step objects one at a time as the loop
        progresses, so a caller can drive a UI without buffering.
        """
        messages = self._initial_messages(user_message)
        tool_schemas = self.tools.schemas()
        for step_index in range(self.config.max_steps):
            response = self.llm.chat(self._to_llm(messages), tools=tool_schemas)
            if response.tool_call is None:
                step = Step(index=step_index, kind="final", content=response.content)
                if on_step is not None:
                    on_step(step)
                yield step
                return
            tc = response.tool_call
            messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_call=tc,
                )
            )
            step = Step(
                index=step_index,
                kind="llm",
                content=response.content or "",
                tool_name=tc.name,
                tool_result=None,
            )
            if on_step is not None:
                on_step(step)
            yield step
            try:
                raw_result = self.tools.dispatch(tc.name, tc.arguments)
            except Exception as exc:  # noqa: BLE001 - tool errors become tool messages
                raw_result = {"error": f"{type(exc).__name__}: {exc}"}
            result_dict, result_str = _normalize_tool_result(raw_result)
            messages.append(Message(role="tool", name=tc.name, content=result_str))
            step = Step(
                index=step_index,
                kind="tool",
                content=result_str,
                tool_name=tc.name,
                tool_result=result_dict,
            )
            if on_step is not None:
                on_step(step)
            yield step
        step = Step(
            index=self.config.max_steps,
            kind="final",
            content=_MAX_STEPS_REACHED_MESSAGE,
        )
        if on_step is not None:
            on_step(step)
        yield step


__all__ = [
    "AgentResult",
    "ChatResponse",
    "Message",
    "OpenClaude",
    "Step",
    "Tool",
    "ToolCall",
    "ToolRegistry",
]
