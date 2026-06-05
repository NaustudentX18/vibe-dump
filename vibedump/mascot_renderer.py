"""M5 procedural PIL mascot renderer for Dumpi.

Generates a 120x120 PNG per device/dump state with a per-state color palette
and a small accent badge. Pillow is imported lazily so a missing optional
dependency does not break the rest of the package.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    PIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Palette and labels
# ---------------------------------------------------------------------------

WIDTH = 120
HEIGHT = 120
MASCOT_NAME = "Dumpi"

# (background primary, background shade, mascot body, eye color)
PALETTE: dict[str, tuple[str, str, str, str]] = {
    "idle":      ("#b78a3a", "#7a5520", "#8a5f1a", "#1c1308"),
    "listening": ("#4ad6ff", "#1d8aa6", "#2c9fc6", "#06222b"),
    "thinking":  ("#a07cff", "#6045b8", "#7a5cd0", "#1c0e3a"),
    "speaking":  ("#58e0a4", "#26805a", "#36b888", "#0e2b1d"),
    "error":     ("#ff647c", "#a8243e", "#d04058", "#3b0a16"),
    "level_up":  ("#ffd866", "#b88f1d", "#f0c040", "#3a2700"),
    "sleeping":  ("#5a78a8", "#324668", "#406090", "#0c1626"),
    "draft":     ("#888888", "#4a4a4a", "#666666", "#111111"),
    "ready":     ("#3ed68b", "#1d7a4d", "#28b070", "#06231a"),
}

LABELS: dict[str, str] = {
    "idle":      "Dumpi: idle",
    "listening": "Dumpi: listening",
    "thinking":  "Dumpi: thinking",
    "speaking":  "Dumpi: speaking",
    "error":     "Dumpi: error",
    "level_up":  "Dumpi: level up!",
    "sleeping":  "Dumpi: sleeping",
    "draft":     "Dumpi: draft",
    "ready":     "Dumpi: ready",
}


def _hex(rgb: str) -> tuple[int, int, int]:
    """Parse '#rrggbb' into a 3-tuple of channel ints."""
    return (int(rgb[1:3], 16), int(rgb[3:5], 16), int(rgb[5:7], 16))


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MascotFrame:
    state: str
    label: str
    png_bytes: bytes
    width: int = WIDTH
    height: int = HEIGHT

    def to_png(self) -> bytes:
        return self.png_bytes


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class MascotRenderer:
    """Procedural Dumpi renderer with a class-level frame cache."""

    _cache: ClassVar[dict[str, bytes]] = {}

    def render(self, state: str) -> MascotFrame:
        key = self._normalize(state)
        render_key = key if key in PALETTE else "idle"
        label = LABELS.get(key, f"{MASCOT_NAME}: {key}")

        if not PIL_AVAILABLE:
            return MascotFrame(
                state=key,
                label="(no pillow)",
                png_bytes=b"",
                width=WIDTH,
                height=HEIGHT,
            )

        cached = self._cache.get(render_key)
        if cached is None:
            cached = self._render_frame(render_key)
            self._cache[render_key] = cached

        return MascotFrame(
            state=key,
            label=label,
            png_bytes=cached,
            width=WIDTH,
            height=HEIGHT,
        )

    @staticmethod
    def _normalize(state: Any) -> str:
        if hasattr(state, "value"):
            return str(state.value)
        return str(state).strip().lower()

    @staticmethod
    def _render_frame(key: str) -> bytes:
        if Image is None:
            return b""
        path = Path(__file__).parent / "static" / "mascot" / f"{key}.png"
        if not path.exists():
            path = Path(__file__).parent / "static" / "mascot" / "idle.png"
        if not path.exists():
            return b""
        try:
            with Image.open(path) as img:
                if img.size != (WIDTH, HEIGHT):
                    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                return buf.getvalue()
        except Exception:
            return b""


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------


FakeMascotRenderer = MascotRenderer
