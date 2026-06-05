"""Tests for vibedump.agent.swarm and swarm_executor."""

from __future__ import annotations

import asyncio

import pytest

from vibedump.agent.swarm import (
    SWARM_V1,
    NodeSpec,
    SwarmContext,
    SwarmResult,
)
from vibedump.agent.swarm_executor import SwarmExecutor, run_swarm_sync


# ---------------------------------------------------------------------------
# SwarmExecutor (driven via asyncio.run to avoid pytest-asyncio dependency)
# ---------------------------------------------------------------------------


def test_swarm_v1_completes_all_nodes():
    ctx = SwarmContext(state={"transcript": "I want a todo app"})
    result = asyncio.run(SwarmExecutor(SWARM_V1).run(ctx))

    assert isinstance(result, SwarmResult)
    assert result.ok, f"errors: {result.errors}"
    assert set(result.node_results) == {"architect", "critic", "security"}
    assert result.node_results["architect"] is not None


def test_swarm_respects_dependency_order():
    order: list[str] = []

    async def node_a(ctx: SwarmContext):
        order.append("a")
        return "a_done"

    async def node_b(ctx: SwarmContext):
        order.append("b")
        return "b_done"

    async def node_c(ctx: SwarmContext):
        order.append("c")
        return "c_done"

    nodes = [
        NodeSpec(name="a", handler=node_a, depends_on=()),
        NodeSpec(name="b", handler=node_b, depends_on=("a",)),
        NodeSpec(name="c", handler=node_c, depends_on=("b",)),
    ]
    result = asyncio.run(SwarmExecutor(nodes).run())

    assert result.ok
    assert order == ["a", "b", "c"]


def test_swarm_captures_node_errors():
    async def bad_node(ctx: SwarmContext):
        raise ValueError("intentional failure")

    nodes = [NodeSpec(name="bad", handler=bad_node, depends_on=())]
    result = asyncio.run(SwarmExecutor(nodes).run())

    assert not result.ok
    assert "bad" in result.errors
    assert "ValueError" in result.errors["bad"]


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


def test_run_swarm_sync_returns_result():
    ctx = SwarmContext(state={"transcript": "sync test"})
    result = run_swarm_sync(SWARM_V1, ctx)
    assert isinstance(result, SwarmResult)
    assert result.ok
    assert "architect" in result.node_results
    assert "security" in result.node_results


def test_swarm_parallel_nodes_both_run():
    """Two independent nodes with no dependencies should both run."""
    ran: list[str] = []

    async def node_x(ctx: SwarmContext):
        ran.append("x")

    async def node_y(ctx: SwarmContext):
        ran.append("y")

    nodes = [
        NodeSpec(name="x", handler=node_x, depends_on=()),
        NodeSpec(name="y", handler=node_y, depends_on=()),
    ]
    result = run_swarm_sync(nodes)
    assert result.ok
    assert set(ran) == {"x", "y"}
