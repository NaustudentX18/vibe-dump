"""FTS-backed MVP RAG memory."""

from __future__ import annotations

from dataclasses import dataclass

from .database import Database


@dataclass(frozen=True, slots=True)
class MemoryChunk:
    dump_id: int
    source_type: str
    source_id: int
    chunk_index: int
    content: str


class RagMemory:
    def __init__(self, db: Database, chunk_size: int = 600, overlap: int = 80) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.db = db
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str) -> list[str]:
        normalized = " ".join(text.split())
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + self.chunk_size)
            chunks.append(normalized[start:end])
            if end == len(normalized):
                break
            start = max(0, end - self.overlap)
        return chunks

    def remember(self, dump_id: int, source_type: str, source_id: int, text: str) -> list[int]:
        ids: list[int] = []
        for idx, chunk in enumerate(self.chunk_text(text)):
            ids.append(self.db.add_chunk(dump_id, source_type, source_id, idx, chunk))
        return ids

    def search(self, query: str, limit: int = 10) -> list[dict]:
        return self.db.search_chunks(query, limit=limit)
