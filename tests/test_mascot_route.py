"""Tests for the M5 /api/mascot/{state}.png PNG route."""

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


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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


def test_mascot_idle_returns_png(client: TestClient) -> None:
    res = client.get("/api/mascot/idle.png")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    assert res.content[:8] == PNG_MAGIC


def test_mascot_listening_returns_png(client: TestClient) -> None:
    res = client.get("/api/mascot/listening.png")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    assert res.content[:8] == PNG_MAGIC
    assert len(res.content) > 50  # real frame, not just the magic header


def test_mascot_ready_returns_png(client: TestClient) -> None:
    res = client.get("/api/mascot/ready.png")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/png")
    assert res.content[:8] == PNG_MAGIC


def test_mascot_unknown_state_returns_404(client: TestClient) -> None:
    res = client.get("/api/mascot/totally-not-a-state.png")
    assert res.status_code == 404
    assert "unknown" in res.json()["detail"]


def test_mascot_same_state_returns_same_bytes(client: TestClient) -> None:
    """Renderer has a class-level cache; the route should serve the same bytes."""
    a = client.get("/api/mascot/thinking.png")
    b = client.get("/api/mascot/thinking.png")
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.content == b.content


def test_mascot_includes_cache_control_header(client: TestClient) -> None:
    res = client.get("/api/mascot/idle.png")
    assert res.status_code == 200
    cache_control = res.headers.get("cache-control", "")
    assert "max-age" in cache_control


def test_mascot_error_state_is_valid_png(client: TestClient) -> None:
    """The 'error' palette must also produce a real PNG."""
    res = client.get("/api/mascot/error.png")
    assert res.status_code == 200
    assert res.content[:8] == PNG_MAGIC


def test_mascot_degraded_mode_when_pillow_missing(client: TestClient) -> None:
    """If the renderer returns empty bytes (no Pillow), the route must not 500.

    It should return a 200 with a valid fallback PNG and an X-Mascot-Mode
    header set to ``degraded``.
    """
    from vibedump import app as app_module

    class _EmptyRenderer:
        def render(self, state: str):  # type: ignore[no-untyped-def]
            from vibedump.mascot_renderer import MascotFrame

            return MascotFrame(state=state, label="empty", png_bytes=b"")

    state: AppState = client.app.state.vibedump  # type: ignore[attr-defined]
    real_renderer = state.mascot_renderer
    state.mascot_renderer = _EmptyRenderer()  # type: ignore[assignment]
    try:
        res = client.get("/api/mascot/idle.png")
    finally:
        state.mascot_renderer = real_renderer
        # Re-touch app_module to keep mypy aware; not actually required.
        del app_module

    assert res.status_code == 200
    assert res.content[:8] == PNG_MAGIC
    assert res.headers.get("x-mascot-mode") == "degraded"
