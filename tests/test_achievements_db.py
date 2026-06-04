"""Tests for the M5 achievements + profile DB helpers."""

from __future__ import annotations

import pytest

from vibedump.database import (
    DEFAULT_ACHIEVEMENTS,
    AchievementRecord,
    Database,
    ProfileRecord,
)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "achievements.sqlite3")
    database.initialize()
    yield database
    database.close()


def test_initialize_seeds_all_default_achievements_locked(db):
    achievements = db.list_achievements()

    assert len(achievements) == len(DEFAULT_ACHIEVEMENTS)
    assert [a.key for a in achievements] == [row[0] for row in DEFAULT_ACHIEVEMENTS]
    assert all(a.unlocked_at is None for a in achievements)
    # Every record is the expected dataclass type.
    assert all(isinstance(a, AchievementRecord) for a in achievements)


def test_unlock_achievement_returns_true_then_false(db):
    assert db.unlock_achievement("first_dump") is True
    assert db.unlock_achievement("first_dump") is False


def test_unlock_achievement_persists_unlocked_at(db):
    db.unlock_achievement("first_dump")

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["first_dump"] is not None
    # Other achievements stay locked.
    assert unlocked["first_blueprint"] is None


def test_unlock_unknown_key_returns_false_and_does_not_raise(db):
    assert db.unlock_achievement("not_a_real_achievement") is False


def test_get_profile_returns_seeded_singleton(db):
    profile = db.get_profile()

    assert isinstance(profile, ProfileRecord)
    assert profile.name == "Vibe Coder"
    assert profile.xp == 0
    assert profile.level == 1
    assert profile.streak_days == 0


def test_grant_xp_150_yields_level_2(db):
    profile = db.grant_xp(150)

    assert profile.xp == 150
    assert profile.level == 2  # 1 + 150 // 100


def test_grant_xp_is_cumulative(db):
    first = db.grant_xp(50)
    second = db.grant_xp(50)

    assert first.xp == 50 and first.level == 1
    assert second.xp == 100 and second.level == 2


def test_update_profile_name_succeeds_and_persists(db):
    assert db.update_profile_name("Forest") is True

    profile = db.get_profile()
    assert profile.name == "Forest"
    # Other fields untouched.
    assert profile.xp == 0
    assert profile.level == 1


def test_increment_streak_returns_1_then_2_then_3(db):
    assert db.increment_streak() == 1
    assert db.increment_streak() == 2
    assert db.increment_streak() == 3
    assert db.get_profile().streak_days == 3


def test_reinitialize_does_not_duplicate_achievements(tmp_path):
    path = tmp_path / "reinit.sqlite3"
    database = Database(path)
    database.initialize()

    database.unlock_achievement("first_dump")
    database.grant_xp(75)

    # Re-initialize on the same file: rows must be preserved by INSERT OR IGNORE.
    database.initialize()

    achievements = database.list_achievements()
    assert len(achievements) == len(DEFAULT_ACHIEVEMENTS)
    unlocked = {a.key: a.unlocked_at for a in achievements}
    assert unlocked["first_dump"] is not None
    # Profile state must survive.
    assert database.get_profile().xp == 75

    database.close()
