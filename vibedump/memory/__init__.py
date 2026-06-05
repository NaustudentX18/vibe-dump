"""Vibe-Dump memory subsystem.

Provides :class:`MemoryStore` (FTS5 recall/learn/forget), embedding stubs,
lesson persistence, and shared Pydantic schemas.
"""

from __future__ import annotations

from .embeddings import FakeEmbedder, PiLocalEmbedder
from .lessons import LessonStore
from .schema import LessonRecord, RecallHit
from .store import MemoryStore

__all__ = [
    "FakeEmbedder",
    "LessonRecord",
    "LessonStore",
    "MemoryStore",
    "PiLocalEmbedder",
    "RecallHit",
]
