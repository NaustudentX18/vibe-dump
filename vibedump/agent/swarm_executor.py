"""SwarmExecutor: asyncio-based parallel DAG runner with concurrency cap.

The executor resolves topological order at construction time and runs
nodes in parallel whenever their dependencies have been satisfied,
up to ``max_concurrency`` concurrent tasks.

A sync wrapper :func:`run_swarm_sync` is provided so
:class:`~vibedump.agent_pipeline.AgentPipeline` and other sync callers
can drive the swarm without managing an event loop themselves.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .swarm import NodeSpec, SwarmContext, SwarmResult


class SwarmExecutor:
    """Execute a DAG of :class:`~vibedump.agent.swarm.NodeSpec` nodes.

    Parameters
    ----------
    nodes:
        Ordered list of :class:`~vibedump.agent.swarm.NodeSpec` objects that
        define the DAG.  Cyclic dependencies are not detected at construction
        time — they will cause the executor to stall at runtime.
    max_concurrency:
        Maximum number of node handlers to run simultaneously.
    """

    def __init__(
        self,
        nodes: list[NodeSpec],
        *,
        max_concurrency: int = 2,
    ) -> None:
        self._nodes: dict[str, NodeSpec] = {n.name: n for n in nodes}
        self._max_concurrency = max_concurrency

    async def run(self, ctx: SwarmContext | None = None) -> SwarmResult:
        """Execute all nodes respecting dependency order.

        Nodes with no unmet dependencies are submitted together (up to
        ``max_concurrency`` at a time).  Completed nodes unlock their
        dependants.  Errors in a node are captured in
        :attr:`SwarmResult.errors` rather than raising, so the rest of
        the DAG can still complete.
        """
        if ctx is None:
            ctx = SwarmContext()

        completed: set[str] = set()
        errors: dict[str, str] = {}
        node_results: dict[str, Any] = {}
        sem = asyncio.Semaphore(self._max_concurrency)

        async def _run_node(spec: NodeSpec) -> None:
            async with sem:
                try:
                    result = await spec.handler(ctx)
                    node_results[spec.name] = result
                except Exception as exc:  # noqa: BLE001 - capture, don't propagate
                    errors[spec.name] = f"{type(exc).__name__}: {exc}"
                    node_results[spec.name] = None
                finally:
                    completed.add(spec.name)

        pending = set(self._nodes)
        in_flight: dict[str, asyncio.Task[None]] = {}

        while pending or in_flight:
            # Schedule nodes whose dependencies are now satisfied.
            for name in list(pending):
                spec = self._nodes[name]
                if all(dep in completed for dep in spec.depends_on):
                    if name not in in_flight:
                        task = asyncio.create_task(_run_node(spec), name=name)
                        in_flight[name] = task
                        pending.discard(name)

            if not in_flight:
                # Unresolvable dependency cycle — bail out.
                for name in pending:
                    errors[name] = "dependency cycle or missing dependency"
                break

            # Wait for at least one task to finish.
            done, _ = await asyncio.wait(
                in_flight.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                node_name = task.get_name()
                in_flight.pop(node_name, None)

        return SwarmResult(node_results=node_results, context=ctx, errors=errors)


def run_swarm_sync(
    nodes: list[NodeSpec],
    ctx: SwarmContext | None = None,
    *,
    max_concurrency: int = 2,
) -> SwarmResult:
    """Synchronous wrapper around :class:`SwarmExecutor`.

    Creates (or reuses) an event loop and drives the executor to
    completion.  Safe to call from a non-async context such as
    :class:`~vibedump.agent_pipeline.AgentPipeline`.
    """
    executor = SwarmExecutor(nodes, max_concurrency=max_concurrency)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Inside an already-running loop (e.g. pytest-asyncio or Jupyter).
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(asyncio.run, executor.run(ctx))
            return fut.result()

    return asyncio.run(executor.run(ctx))
