from vibedump.database import Database
from vibedump.ragmemory import RagMemory


def test_rag_memory_chunks_and_searches(tmp_path):
    db = Database(tmp_path / "rag.sqlite3")
    db.initialize()
    dump_id = db.create_dump("RAG")
    turn_id = db.add_turn(dump_id, "user", "alpha beta gamma")
    memory = RagMemory(db, chunk_size=20, overlap=5)

    chunk_ids = memory.remember(dump_id, "turn", turn_id, "alpha beta gamma delta epsilon")

    assert len(chunk_ids) >= 2
    results = memory.search("epsilon")
    assert results
    assert results[0]["title"] == "RAG"
    db.close()
