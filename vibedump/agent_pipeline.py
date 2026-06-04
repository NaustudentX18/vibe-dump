"""Fake-first agent pipeline for Milestone 0/1 tests."""

from __future__ import annotations

from dataclasses import dataclass

from .database import Database
from .providers import fake_registry
from .ragmemory import RagMemory
from .schemas import blueprint_template


@dataclass(slots=True)
class PipelineResult:
    dump_id: int
    transcript: str
    blueprint: str


class FakeAgentPipeline:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.registry = fake_registry()
        self.memory = RagMemory(db)

    def ingest_fake_dump(self, title: str, audio_path: str) -> PipelineResult:
        dump_id = self.db.create_dump(title)
        transcript = self.registry.stt["fake"].transcribe(audio_path)
        turn_id = self.db.add_turn(dump_id, "user", transcript, audio_path=audio_path)
        self.memory.remember(dump_id, "turn", turn_id, transcript)
        blueprint = blueprint_template(title)
        blueprint_id = self.db.add_blueprint(dump_id, blueprint)
        self.memory.remember(dump_id, "blueprint", blueprint_id, blueprint)
        return PipelineResult(dump_id=dump_id, transcript=transcript, blueprint=blueprint)
