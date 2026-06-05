"""Tests for vibedump.memory (MemoryStore, embeddings, LessonStore)."""

from __future__ import annotations

import pytest

from vibedump.database import Database
from vibedump.memory import FakeEmbedder, LessonStore, MemoryStore, RecallHit
from vibedump.memory.embeddings import PiLocalEmbedder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db() -> Database:
    db = Database(":memory:")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# MemoryStore.learn + recall
# ---------------------------------------------------------------------------


def test_memory_store_learn_and_recall():
    db = _db()
    store = MemoryStore(db)
    dump_id = db.create_dump("Learn test")
    turn_id = db.add_turn(dump_id, "user", "robots and pandas")

    chunk_ids = store.learn(dump_id, "turn", turn_id, "I want to build a robot assistant for pandas")
    assert len(chunk_ids) >= 1

    hits = store.recall("robot pandas")
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert all(isinstance(h, RecallHit) for h in hits)
    assert any("robot" in h.content.lower() or "pandas" in h.content.lower() for h in hits)


def test_memory_store_recall_empty_query_returns_empty():
    db = _db()
    store = MemoryStore(db)
    hits = store.recall("")
    assert hits == []

    hits2 = store.recall("   ")
    assert hits2 == []


def test_memory_store_forget_removes_chunks():
    db = _db()
    store = MemoryStore(db)
    dump_id = db.create_dump("Forget me")
    turn_id = db.add_turn(dump_id, "user", "secret plans")
    store.learn(dump_id, "turn", turn_id, "secret plans for world domination")

    hits_before = store.recall("secret plans")
    assert len(hits_before) >= 1

    store.forget(dump_id)

    hits_after = store.recall("secret plans")
    assert hits_after == []


# ---------------------------------------------------------------------------
# Embedder stubs
# ---------------------------------------------------------------------------


def test_fake_embedder_returns_zero_vectors():
    embedder = FakeEmbedder()
    vecs = embedder.embed(["hello", "world"])
    assert len(vecs) == 2
    assert all(v == 0.0 for vec in vecs for v in vec)
    assert len(vecs[0]) == embedder.dim


def test_pi_local_embedder_stub():
    embedder = PiLocalEmbedder(dim=128)
    vecs = embedder.embed(["test"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 128
    assert all(v == 0.0 for v in vecs[0])


def test_memory_store_embed_delegates_to_embedder():
    db = _db()
    fake = FakeEmbedder()
    store = MemoryStore(db, embedder=fake)
    vecs = store.embed(["alpha", "beta"])
    assert len(vecs) == 2
    assert vecs[0] == [0.0] * fake.dim


# ---------------------------------------------------------------------------
# LessonStore
# ---------------------------------------------------------------------------


def test_lesson_store_add_and_search():
    db = _db()
    ls = LessonStore(db)
    lesson_id = ls.add("Always validate user input before processing", tags=["security"])
    assert lesson_id > 0

    results = ls.search("validate input")
    assert len(results) >= 1
    assert any("validate" in r.text.lower() for r in results)


def test_lesson_store_list_all():
    db = _db()
    ls = LessonStore(db)
    ls.add("First lesson about testing")
    ls.add("Second lesson about design")

    all_lessons = ls.list_all()
    assert len(all_lessons) == 2
    assert all_lessons[0].text == "First lesson about testing"


def test_lesson_store_delete():
    db = _db()
    ls = LessonStore(db)
    lid = ls.add("Temporary lesson to delete")
    assert ls.delete(lid) is True
    assert ls.delete(lid) is False  # already gone
    assert ls.list_all() == []
