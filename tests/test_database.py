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


def test_search_chunks_handles_literal_user_punctuation(tmp_path):
    db = Database(tmp_path / "search.sqlite3")
    db.initialize()
    dump_id = db.create_dump("Natural Search")
    turn_id = db.add_turn(dump_id, "user", "voice-first Pi Zero idea")
    db.add_chunk(dump_id, "turn", turn_id, 0, "voice-first RAG memory for Pi Zero 2 W")

    assert db.search_chunks("voice-first")[0]["dump_id"] == dump_id
    assert db.search_chunks("???") == []
    db.close()


def test_list_dumps_orders_newest_first_and_paginates(tmp_path):
    db = Database(tmp_path / "list.sqlite3")
    db.initialize()
    ids = [db.create_dump(f"dump-{i}") for i in range(5)]
    assert ids == sorted(ids)

    page = db.list_dumps(limit=2, offset=1)
    assert [d.id for d in page] == [ids[-2], ids[-3]]
    assert all(d.title.startswith("dump-") for d in page)
    db.close()


def test_update_dump_status_returns_true_then_false(tmp_path):
    db = Database(tmp_path / "status.sqlite3")
    db.initialize()
    dump_id = db.create_dump("status test")
    initial = db.get_dump(dump_id)
    assert initial is not None and initial.status == "draft"

    assert db.update_dump_status(dump_id, "listening") is True
    after = db.get_dump(dump_id)
    assert after is not None and after.status == "listening"
    assert db.update_dump_status(999, "listening") is False
    db.close()


def test_list_turns_returns_in_order(tmp_path):
    db = Database(tmp_path / "turns.sqlite3")
    db.initialize()
    dump_id = db.create_dump("turn order")
    db.add_turn(dump_id, "user", "first")
    db.add_turn(dump_id, "assistant", "second")
    db.add_turn(dump_id, "user", "third")

    turns = db.list_turns(dump_id)
    assert [t.text for t in turns] == ["first", "second", "third"]
    assert [t.role for t in turns] == ["user", "assistant", "user"]
    db.close()


def test_latest_blueprint_returns_most_recent(tmp_path):
    db = Database(tmp_path / "bp.sqlite3")
    db.initialize()
    dump_id = db.create_dump("blueprint")
    db.add_blueprint(dump_id, "# older\n")
    newer = db.add_blueprint(dump_id, "# newer\n")

    bp = db.get_latest_blueprint(dump_id)
    assert bp is not None
    assert bp.id == newer
    assert "newer" in bp.markdown
    db.close()


def test_provider_config_upsert_replaces_existing(tmp_path):
    db = Database(tmp_path / "provider.sqlite3")
    db.initialize()

    first = db.upsert_provider_config("openrouter", "llm", False, {"api_key": "old"})
    second = db.upsert_provider_config("openrouter", "llm", True, {"api_key": "new", "model": "x"})

    configs = {c.name: c for c in db.list_provider_configs()}
    assert configs["openrouter"].enabled is True
    assert configs["openrouter"].config == {"api_key": "new", "model": "x"}
    assert first == second
    db.close()
