from vibedump.database import Database


def test_profile_seeded_for_xp_system(tmp_path):
    db = Database(tmp_path / "xp.sqlite3")
    db.initialize()
    with db.transaction() as conn:
        row = conn.execute("SELECT xp, level FROM profile WHERE id = 1").fetchone()
    assert dict(row) == {"xp": 0, "level": 1}
    db.close()
