"""Tests for M5 /api/achievements and /api/profile routes."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from vibedump.app import AppState, create_app  # noqa: E402
from vibedump.database import Database  # noqa: E402
from vibedump.events import EventBus  # noqa: E402
from vibedump.agent_pipeline import FakeAgentPipeline  # noqa: E402
from vibedump.ragmemory import RagMemory  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    db = Database(":memory:")
    db.initialize()
    state = AppState(
        db=db,
        bus=EventBus(),
        pipeline=FakeAgentPipeline(db),
        memory=RagMemory(db),
    )
    return TestClient(create_app(state))


def test_achievements_returns_five_items_all_locked(client: TestClient) -> None:
    res = client.get("/api/achievements")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 5
    for item in body["items"]:
        assert set(item.keys()) == {"key", "title", "description", "unlocked_at"}
        assert item["unlocked_at"] is None


def test_achievements_includes_unlocked_and_total_counts(client: TestClient) -> None:
    res = client.get("/api/achievements")
    assert res.status_code == 200
    body = res.json()
    assert body["unlocked_count"] == 0
    assert body["total"] == 5
    assert body["unlocked_count"] == sum(
        1 for i in body["items"] if i["unlocked_at"] is not None
    )


def test_profile_default_values(client: TestClient) -> None:
    res = client.get("/api/profile")
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "name": "Vibe Coder",
        "xp": 0,
        "level": 1,
        "streak_days": 0,
        "xp_to_next_level": 100,
    }


def test_patch_profile_updates_name(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"name": "Forest"})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Forest"
    assert body["xp"] == 0
    assert body["level"] == 1
    assert body["xp_to_next_level"] == 100

    # Profile is persisted.
    follow = client.get("/api/profile").json()
    assert follow["name"] == "Forest"


def test_patch_profile_grant_xp_increases_xp_and_level(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"xp_delta": 50})
    assert res.status_code == 200
    body = res.json()
    assert body["xp"] == 50
    assert body["level"] == 1
    assert body["xp_to_next_level"] == 50


def test_patch_profile_negative_xp_rejected(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"xp_delta": -1})
    assert res.status_code == 400
    assert "non-negative" in res.json()["detail"]


def test_patch_profile_empty_name_rejected(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"name": ""})
    assert res.status_code == 400


def test_patch_profile_combined_name_and_xp_delta(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"name": "Forest", "xp_delta": 25})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Forest"
    assert body["xp"] == 25
    assert body["xp_to_next_level"] == 75
