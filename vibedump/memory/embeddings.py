"""Embedder stubs for the memory subsystem.

FakeEmbedder returns zero vectors so tests run without sentence-transformers.
PiLocalEmbedder is a stub that mimics the real Pi-compatible embedder interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Minimal embedder interface used by MemoryStore."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Returns zero vectors. Safe to use in tests without ML dependencies."""

    dim: int = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]


class PiLocalEmbedder:
    """Stub for a Pi-local embedding service (no network round-trip required).

    Intended to wrap a future sentence-transformers or llama.cpp embedder.
    In the stub state it behaves identically to FakeEmbedder.
    """

    def __init__(self, model_name: str = "all-minilm-l6-v2", dim: int = 384) -> None:
        self.model_name = model_name
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Stub: real implementation would call a local embedding endpoint.
        return [[0.0] * self.dim for _ in texts]
