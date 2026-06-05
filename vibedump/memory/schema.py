"""Pydantic models for the memory subsystem."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecallHit(BaseModel):
    """One FTS5 search result from the memory store."""

    content: str
    title: str = ""
    dump_id: int = 0
    chunk_id: int = 0
    score: float = 0.0


class LessonRecord(BaseModel):
    """A persisted lesson from the EvolutionHook."""

    id: int = 0
    text: str
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
