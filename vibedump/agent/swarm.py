"""Swarm primitives: DAG node spec, context, and result types.

The SWARM_V1 DAG is a minimal three-node graph that mirrors the
ArchitectNode / CriticNode / SecurityNode sequence already used by
:class:`~vibedump.agent_pipeline.GraphPipeline`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


# ---------------------------------------------------------------------------
# Node spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Declarative description of one node in a swarm DAG.

    Parameters
    ----------
    name:
        Unique node identifier.
    handler:
        ``async`` callable ``(SwarmContext) -> Any`` that performs the work.
    depends_on:
        Names of nodes that must complete before this one is started.
    """

    name: str
    handler: Callable[["SwarmContext"], Coroutine[Any, Any, Any]]
    depends_on: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Context / result
# ---------------------------------------------------------------------------


@dataclass
class SwarmContext:
    """Shared mutable context passed to every node handler.

    Nodes read from and write to ``state`` freely; the executor guarantees
    that only nodes whose ``depends_on`` have completed will run concurrently.
    """

    state: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SwarmResult:
    """The final outcome of a swarm execution."""

    node_results: dict[str, Any]
    context: SwarmContext
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Built-in SWARM_V1 DAG definition
# ---------------------------------------------------------------------------


async def _architect_handler(ctx: SwarmContext) -> str:
    """Generates an initial blueprint draft."""
    draft = ctx.state.get("transcript", "(empty transcript)")
    result = f"[Architect] blueprint draft from: {draft!s:.80}"
    ctx.results["architect"] = result
    return result


async def _critic_handler(ctx: SwarmContext) -> str:
    """Reviews and refines the architect's draft."""
    draft = ctx.results.get("architect", "")
    result = f"[Critic] reviewed: {draft!s:.80}"
    ctx.results["critic"] = result
    return result


async def _security_handler(ctx: SwarmContext) -> str:
    """Performs security review and finalises the blueprint."""
    reviewed = ctx.results.get("critic", "")
    result = f"[Security] cleared: {reviewed!s:.80}"
    ctx.results["security"] = result
    return result


SWARM_V1: list[NodeSpec] = [
    NodeSpec(name="architect", handler=_architect_handler, depends_on=()),
    NodeSpec(name="critic", handler=_critic_handler, depends_on=("architect",)),
    NodeSpec(name="security", handler=_security_handler, depends_on=("critic",)),
]
