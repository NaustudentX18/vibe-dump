"""Tests for the M5 XP grants + achievement unlocks wired into AgentPipeline.

These tests pin down the gamification side-effects the pipeline now emits:

- ``start_dump`` grants 5 XP and unlocks ``first_dump``.
- The one-shot flow grants 20 XP and unlocks ``first_blueprint``.
- ``step_listener`` FINALIZE grants 20 XP and unlocks ``first_blueprint``.
- Milestone unlocks (``level_2``, ``xp_100``, ``streak_3``) fire when
  thresholds are crossed.
- ``profile.xp`` and ``profile.level_up`` events are published on the
  bus with the documented payloads.
- Unlocking an unknown key is a silent no-op.
- The pipeline still works when no bus is supplied.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from vibedump.agent_pipeline import AgentPipeline
from vibedump.database import Database
from vibedump.events import EventBus
from vibedump.providers import ProviderRegistry
from vibedump.providers.base import LLMProvider, ProviderHealth
from vibedump.providers.stt import FakeSTT
from vibedump.providers.tts import FakeTTS
from vibedump.schemas import blueprint_template


# ---------------------------------------------------------------------------
# Stubs (mirrors the test_active_listener_pipeline fixture pattern)
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Stub LLM that returns a scripted sequence of responses."""

    def __init__(self, *responses: str, name: str = "scripted") -> None:
        self._responses: list[str] = list(responses)
        self._calls: list[str] = []
        self.name = name
        self.configured = True

    def complete(self, prompt: str) -> str:
        self._calls.append(prompt)
        if not self._responses:
            raise AssertionError("scripted LLM ran out of responses")
        return self._responses.pop(0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "scripted stub ready")


def _registry_with(llm: LLMProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm[llm.name] = llm
    registry.tts["fake"] = FakeTTS()
    return registry


@pytest.fixture
def pipeline() -> Iterator[tuple[AgentPipeline, _ScriptedLLM, Database, EventBus]]:
    db = Database(":memory:")
    db.initialize()
    bus = EventBus()
    llm = _ScriptedLLM()
    p = AgentPipeline(db, _registry_with(llm), bus=bus, llm=llm)
    yield p, llm, db, bus
    db.close()


def _events(bus: EventBus, event_type: str) -> list[dict[str, Any]]:
    return [e.payload for e in bus.snapshot() if e.event_type == event_type]


# ---------------------------------------------------------------------------
# XP grants on start_dump
# ---------------------------------------------------------------------------


def test_start_dump_grants_5_xp_and_unlocks_first_dump(pipeline: Any) -> None:
    p, _, db, bus = pipeline
    p.start_dump("Test")

    profile = db.get_profile()
    assert profile.xp == 5
    assert profile.level == 1

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["first_dump"] is not None
    # first_blueprint not yet unlocked — no blueprint yet.
    assert unlocked["first_blueprint"] is None

    xp_events = _events(bus, "profile.xp")
    assert len(xp_events) == 1
    assert xp_events[0]["delta"] == 5
    assert xp_events[0]["reason"] == "start_dump"
    assert xp_events[0]["xp"] == 5
    assert xp_events[0]["level"] == 1

    ach_events = _events(bus, "achievement.unlocked")
    assert ach_events == [{"key": "first_dump", "title": "First Dump"}]


def test_start_dump_idempotent_first_dump_unlock(pipeline: Any) -> None:
    p, _, db, bus = pipeline
    p.start_dump("A")
    p.start_dump("B")

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["first_dump"] is not None

    # Only one achievement.unlocked event for first_dump should ever publish.
    first_dump_events = [
        e for e in _events(bus, "achievement.unlocked") if e["key"] == "first_dump"
    ]
    assert len(first_dump_events) == 1

    # start_dump also grants XP each time, so profile.xp accumulates.
    assert db.get_profile().xp == 10


# ---------------------------------------------------------------------------
# XP grants on the blueprint flows
# ---------------------------------------------------------------------------


def test_start_dump_then_finalize_grants_25_xp_and_unlocks_first_blueprint(
    pipeline: Any,
) -> None:
    p, llm, db, bus = pipeline
    dump_id = p.start_dump("Test", audio_path="idea.wav")
    llm._responses = [
        "[FINALIZE] we have enough context",
        blueprint_template("Test"),
    ]
    p.step_listener(dump_id)

    # 5 from start_dump + 20 from finalize = 25.
    profile = db.get_profile()
    assert profile.xp == 25
    assert profile.level == 1

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["first_blueprint"] is not None

    xp_events = _events(bus, "profile.xp")
    assert [e["delta"] for e in xp_events] == [5, 20]
    assert [e["reason"] for e in xp_events] == ["start_dump", "blueprint_finalize"]

    ach_events = _events(bus, "achievement.unlocked")
    keys = [e["key"] for e in ach_events]
    assert "first_dump" in keys
    assert "first_blueprint" in keys


def test_ingest_fake_dump_grants_20_xp_and_unlocks_first_blueprint(
    pipeline: Any,
) -> None:
    p, _, db, bus = pipeline
    p.ingest_fake_dump("Test", "idea.wav")

    profile = db.get_profile()
    assert profile.xp == 20

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["first_blueprint"] is not None

    xp_events = _events(bus, "profile.xp")
    assert len(xp_events) == 1
    assert xp_events[0]["delta"] == 20
    assert xp_events[0]["reason"] == "blueprint_generated"

    ach_events = _events(bus, "achievement.unlocked")
    assert ach_events == [{"key": "first_blueprint", "title": "Spec Goblin"}]


# ---------------------------------------------------------------------------
# Milestone unlocks
# ---------------------------------------------------------------------------


def test_xp_100_milestone_unlocks_after_100_xp(pipeline: Any) -> None:
    p, _, db, bus = pipeline
    # 5 XP per start_dump * 20 = 100.
    for i in range(20):
        p.start_dump(f"dump-{i}")

    profile = db.get_profile()
    assert profile.xp == 100
    assert profile.level == 2

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["xp_100"] is not None
    assert unlocked["level_2"] is not None  # also crossed the level-2 threshold.

    ach_events = _events(bus, "achievement.unlocked")
    assert any(e["key"] == "xp_100" for e in ach_events)
    assert any(e["key"] == "level_2" for e in ach_events)


def test_level_2_unlocks_and_level_up_event_fires(pipeline: Any) -> None:
    p, _, db, bus = pipeline
    p._grant_xp_with_event(200, "test_jump")

    profile = db.get_profile()
    assert profile.level == 3
    assert profile.xp == 200

    unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
    assert unlocked["level_2"] is not None

    level_up_events = _events(bus, "profile.level_up")
    # A 200-XP grant from level 1 should produce exactly one level_up event.
    assert level_up_events == [{"level": 3}]


def test_profile_xp_event_payload_shape(pipeline: Any) -> None:
    p, _, _, bus = pipeline
    p.start_dump("Test")

    xp_events = _events(bus, "profile.xp")
    assert len(xp_events) == 1
    payload = xp_events[0]
    assert set(payload.keys()) == {"xp", "level", "delta", "reason"}
    assert isinstance(payload["xp"], int)
    assert isinstance(payload["level"], int)
    assert isinstance(payload["delta"], int)
    assert isinstance(payload["reason"], str)


def test_achievement_unlocked_event_payload_includes_key_and_title(
    pipeline: Any,
) -> None:
    p, _, _, bus = pipeline
    p.start_dump("Test")

    ach_events = _events(bus, "achievement.unlocked")
    assert len(ach_events) == 1
    assert set(ach_events[0].keys()) == {"key", "title"}
    assert ach_events[0] == {"key": "first_dump", "title": "First Dump"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_unlock_unknown_key_is_silent_noop(pipeline: Any) -> None:
    p, _, db, bus = pipeline
    p._unlock("not_a_real_achievement")
    p._unlock("also_bogus")

    # No achievements unlocked, no events published.
    assert all(a.unlocked_at is None for a in db.list_achievements())
    assert _events(bus, "achievement.unlocked") == []


def test_pipeline_works_without_bus() -> None:
    """Constructing the pipeline with bus=None must not break the XP/achievement flow."""
    db = Database(":memory:")
    db.initialize()
    llm = _ScriptedLLM()
    p = AgentPipeline(db, _registry_with(llm), bus=None, llm=llm)
    try:
        dump_id = p.start_dump("Test", audio_path="idea.wav")
        # start_dump itself should still grant XP and unlock first_dump.
        assert db.get_profile().xp == 5
        unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
        assert unlocked["first_dump"] is not None

        # FINALIZE path should also work without a bus.
        llm._responses = [
            "[FINALIZE] enough",
            blueprint_template("Test"),
        ]
        result = p.step_listener(dump_id)
        assert result.status == "ready"
        assert db.get_profile().xp == 25
        unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
        assert unlocked["first_blueprint"] is not None

        # Jumping levels still unlocks milestones when bus is None.
        p._grant_xp_with_event(200, "manual_jump")
        profile = db.get_profile()
        assert profile.xp == 225
        assert profile.level == 3  # 1 + 225//100 = 3
        unlocked = {a.key: a.unlocked_at for a in db.list_achievements()}
        assert unlocked["level_2"] is not None
        assert unlocked["xp_100"] is not None
    finally:
        db.close()
