from vibedump.database import Database


def test_database_initializes_wal_foreign_keys_and_fts(tmp_path):
    db = Database(tmp_path / "vibedump.sqlite3")
    db.initialize()

    assert str(db.pragma("journal_mode")).lower() == "wal"
    assert db.pragma("foreign_keys") == 1

    dump_id = db.create_dump("Pocket Manus")
    turn_id = db.add_turn(dump_id, "user", "Build a tiny voice first spec goblin")
    chunk_id = db.add_chunk(dump_id, "turn", turn_id, 0, "tiny voice spec goblin memory")

    results = db.search_chunks("goblin")
    assert results[0]["dump_id"] == dump_id
    assert results[0]["chunk_id"] == chunk_id
    assert "goblin" in results[0]["content"]

    db.close()


def test_delete_dump_cascades_rows_and_fts(tmp_path):
    db = Database(tmp_path / "cascade.sqlite3")
    db.initialize()
    dump_id = db.create_dump("Delete Me")
    turn_id = db.add_turn(dump_id, "user", "delete cascade searchable text")
    db.add_blueprint(dump_id, "# Vibe Coding Blueprint\n")
    db.add_chunk(dump_id, "turn", turn_id, 0, "cascade searchable text")

    assert db.count("turns") == 1
    assert db.search_chunks("searchable")

    db.delete_dump(dump_id)

    assert db.count("dumps") == 0
    assert db.count("turns") == 0
    assert db.count("blueprints") == 0
    assert db.count("chunks") == 0
    assert db.search_chunks("searchable") == []
    db.close()
