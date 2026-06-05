from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any
from vibedump.agent_pipeline import GraphPipeline, AgentPipeline, DumpState, _GRAPH_STATE_KEY
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS
from vibedump.schemas import blueprint_template
from vibedump.ragmemory import RagMemory

@dataclass
class ScriptedLLM:
    name: str = "scripted"
    configured: bool = True
    calls: list[str] = field(default_factory=list)
    responses: list[str] = field(
        default_factory=lambda: [
            "[FINALIZE] enough information gathered",
            blueprint_template("Swarms Test Dump"),
        ]
    )

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted")

@pytest.fixture
def graph_setup() -> tuple[GraphPipeline, Database]:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    registry: Any = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    llm_stub: Any = ScriptedLLM()
    registry.llm["scripted"] = llm_stub
    registry.tts["fake"] = FakeTTS()
    
    db.upsert_provider_config("scripted", "llm", True, {})

    p = AgentPipeline(db, registry=registry, bus=bus)
    graph = GraphPipeline(p)
    return graph, db

def test_graph_swarms_sequential_execution(graph_setup: tuple[GraphPipeline, Database]) -> None:
    graph, db = graph_setup
    
    # 1. Start a dump
    dump_id = graph._pipeline.start_dump("Swarms Test Dump", audio_path="idea.wav")
    
    # 2. Run the graph to finalize
    blueprint = graph.run(dump_id, max_steps=5)

    # 3. Verify final status is ready
    dump = db.get_dump(dump_id)
    assert dump.status == "ready"

    # 4. Verify all three multi-agent nodes were executed in sequence
    raw_state = dump.metadata.get(_GRAPH_STATE_KEY)
    assert raw_state
    state = DumpState.model_validate_json(raw_state)
    
    assert state.visited_nodes == ["ArchitectNode", "CriticNode", "SecurityNode"]
    assert state.current_status == "ready"

    # 5. Verify the blueprint contains modifications from all three nodes
    assert "## 1." in blueprint
    assert "<!-- Critic: Reviewed and approved -->" in blueprint
    assert "<!-- Security: Cleared for publication -->" in blueprint
