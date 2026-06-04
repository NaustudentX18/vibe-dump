"""Tests for the M5 PIL mascot renderer."""

from __future__ import annotations

import pytest

from vibedump import mascot_renderer
from vibedump.mascot_renderer import (
    FakeMascotRenderer,
    MascotFrame,
    MascotRenderer,
    PIL_AVAILABLE,
)
from vibedump.state import DeviceState


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def renderer() -> MascotRenderer:
    return MascotRenderer()


# ---------------------------------------------------------------------------
# Smoke + label
# ---------------------------------------------------------------------------


def test_render_idle_returns_dumpi_frame(renderer: MascotRenderer) -> None:
    frame = renderer.render("idle")
    assert isinstance(frame, MascotFrame)
    assert frame.png_bytes != b""
    assert "Dumpi" in frame.label
    assert frame.state == "idle"
    assert frame.width == 120
    assert frame.height == 120


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_render_caches_bytes_per_state(renderer: MascotRenderer) -> None:
    first = renderer.render("listening")
    second = renderer.render("listening")
    assert first.png_bytes == second.png_bytes
    # Cached lookup returns the same object (ClassVar cache).
    assert first.png_bytes is second.png_bytes
    # Cache key is the same regardless of caller identity.
    assert renderer._cache["listening"] == first.png_bytes


# ---------------------------------------------------------------------------
# Every known state renders without raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ds.value for ds in DeviceState])
def test_every_device_state_renders(renderer: MascotRenderer, state: str) -> None:
    frame = renderer.render(state)
    assert frame.png_bytes.startswith(PNG_MAGIC)
    assert "Dumpi" in frame.label


@pytest.mark.parametrize("state", ["draft", "ready", "listening", "thinking"])
def test_every_dump_status_renders(renderer: MascotRenderer, state: str) -> None:
    frame = renderer.render(state)
    assert frame.png_bytes.startswith(PNG_MAGIC)
    assert "Dumpi" in frame.label


# ---------------------------------------------------------------------------
# Unknown state falls back gracefully
# ---------------------------------------------------------------------------


def test_unknown_state_falls_back_to_idle(renderer: MascotRenderer) -> None:
    frame = renderer.render("nonsense_state")
    assert frame.png_bytes.startswith(PNG_MAGIC)
    # Falls back to the idle palette, so the bytes match a known idle render.
    idle = renderer.render("idle")
    assert frame.png_bytes == idle.png_bytes


# ---------------------------------------------------------------------------
# PNG validity
# ---------------------------------------------------------------------------


def test_png_bytes_have_valid_magic(renderer: MascotRenderer) -> None:
    frame = renderer.render("thinking")
    assert frame.png_bytes.startswith(PNG_MAGIC)
    # PNGs end with the IEND chunk (49 45 4E 44 ... 00 00 00 00 AE 42 60 82).
    assert frame.png_bytes.endswith(b"\x00\x00\x00\x00IEND\xae\x42\x60\x82")
    assert len(frame.png_bytes) > 8  # not just the magic


# ---------------------------------------------------------------------------
# to_png() helper
# ---------------------------------------------------------------------------


def test_to_png_returns_same_bytes(renderer: MascotRenderer) -> None:
    frame = renderer.render("ready")
    assert frame.to_png() == frame.png_bytes
    assert frame.to_png() is frame.png_bytes  # frozen bytes identity holds


# ---------------------------------------------------------------------------
# Pillow-missing path
# ---------------------------------------------------------------------------


def test_render_without_pillow_returns_empty_bytes(
    renderer: MascotRenderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mascot_renderer, "PIL_AVAILABLE", False)
    # Sanity check: the patch actually took.
    assert mascot_renderer.PIL_AVAILABLE is False
    frame = renderer.render("idle")
    assert frame.png_bytes == b""
    assert frame.label == "(no pillow)"
    assert frame.state == "idle"
    # Even with no pillow, the unknown-state fallback must not raise.
    frame2 = renderer.render("wat")
    assert frame2.png_bytes == b""


# ---------------------------------------------------------------------------
# Visual differentiation
# ---------------------------------------------------------------------------


def test_different_states_produce_different_bytes(
    renderer: MascotRenderer,
) -> None:
    a = renderer.render("idle").png_bytes
    b = renderer.render("listening").png_bytes
    c = renderer.render("error").png_bytes
    d = renderer.render("level_up").png_bytes
    seen = {a, b, c, d}
    assert len(seen) == 4, "expected all four states to render distinct frames"


# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------


def test_fake_mascot_renderer_alias() -> None:
    assert FakeMascotRenderer is MascotRenderer


def test_renderer_accepts_device_state_enum(renderer: MascotRenderer) -> None:
    # The old API passed DeviceState members directly. StrEnum members are
    # str-typed, so the new signature still accepts them.
    frame = renderer.render(DeviceState.IDLE)
    assert frame.state == "idle"
    assert frame.png_bytes.startswith(PNG_MAGIC)
