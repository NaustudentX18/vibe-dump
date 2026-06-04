"""M5 procedural PIL mascot renderer for Dumpi.

Generates a 120x120 PNG per device/dump state with a per-state color palette
and a small accent badge. Pillow is imported lazily so a missing optional
dependency does not break the rest of the package.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, ClassVar

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by mock test
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
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
        if Image is None or ImageDraw is None:
            return b""
        primary_hex, shade_hex, body_hex, eye_hex = PALETTE[key]
        img = Image.new("RGB", (WIDTH, HEIGHT), _hex(primary_hex))
        draw = ImageDraw.Draw(img)

        # Vertical gradient: lighter top, darker bottom of state color.
        primary = _hex(primary_hex)
        shade = _hex(shade_hex)
        for y in range(HEIGHT):
            t = y / (HEIGHT - 1)
            r = int(primary[0] + (shade[0] - primary[0]) * t)
            g = int(primary[1] + (shade[1] - primary[1]) * t)
            b = int(primary[2] + (shade[2] - primary[2]) * t)
            draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

        # Dumpi blob: three stacked ellipses.
        cx, cy = WIDTH // 2, int(HEIGHT * 0.55)
        body = _hex(body_hex)
        body_outline = _hex(shade_hex)
        draw.ellipse((cx - 38, cy - 8,  cx + 38, cy + 36), fill=body, outline=body_outline, width=2)
        draw.ellipse((cx - 30, cy - 30, cx + 30, cy + 4),  fill=body, outline=body_outline, width=2)
        draw.ellipse((cx - 20, cy - 50, cx + 20, cy - 18), fill=body, outline=body_outline, width=2)

        # Eyes on the top ellipse.
        if key == "sleeping":
            draw.line((cx - 11, cy - 34, cx - 5, cy - 34), fill=(245, 245, 250), width=2)
            draw.line((cx + 5,  cy - 34, cx + 11, cy - 34), fill=(245, 245, 250), width=2)
        else:
            draw.ellipse((cx - 11, cy - 38, cx - 5, cy - 30), fill=(255, 255, 255))
            draw.ellipse((cx + 5,  cy - 38, cx + 11, cy - 30), fill=(255, 255, 255))
            eye = _hex(eye_hex)
            draw.ellipse((cx - 9, cy - 35, cx - 6, cy - 32), fill=eye)
            draw.ellipse((cx + 6, cy - 35, cx + 9, cy - 32), fill=eye)

        # Mouth on the middle ellipse.
        if key == "speaking":
            draw.ellipse((cx - 6, cy - 22, cx + 6, cy - 14), fill=(40, 20, 20))
        elif key == "error":
            draw.line((cx - 6, cy - 22, cx + 6, cy - 14), fill=(40, 0, 0), width=2)
            draw.line((cx + 6, cy - 22, cx - 6, cy - 14), fill=(40, 0, 0), width=2)
        else:
            draw.arc((cx - 8, cy - 24, cx + 8, cy - 12), start=20, end=160, fill=(0, 0, 0), width=2)

        _draw_badge(draw, key, body_outline)

        if key == "level_up":
            _draw_sparkles(draw)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers (module-level, used only by _render_frame)
# ---------------------------------------------------------------------------


def _draw_badge(draw: Any, key: str, accent: tuple[int, int, int]) -> None:
    """Render the per-state accent badge in the bottom-right corner."""
    bcx, bcy = WIDTH - 18, HEIGHT - 18
    draw.ellipse((bcx - 12, bcy - 12, bcx + 12, bcy + 12),
                 fill=(255, 255, 255), outline=accent, width=2)

    if key == "error":
        draw.rectangle((bcx - 1, bcy - 5, bcx + 1, bcy + 2), fill=(180, 0, 0))
        draw.ellipse((bcx - 1, bcy + 3, bcx + 1, bcy + 5), fill=(180, 0, 0))
    elif key == "level_up":
        draw.polygon([
            (bcx, bcy - 7), (bcx + 2, bcy - 1),
            (bcx + 7, bcy),     (bcx + 2, bcy + 1),
            (bcx, bcy + 7),     (bcx - 2, bcy + 1),
            (bcx - 7, bcy),     (bcx - 2, bcy - 1),
        ], fill=(255, 215, 0))
    elif key == "sleeping":
        draw.line((bcx - 5, bcy - 4, bcx + 5, bcy - 4), fill=accent, width=2)
        draw.line((bcx + 5, bcy - 4, bcx - 5, bcy + 4), fill=accent, width=2)
        draw.line((bcx - 5, bcy + 4, bcx + 5, bcy + 4), fill=accent, width=2)
    elif key == "listening":
        draw.ellipse((bcx - 4, bcy - 4, bcx + 4, bcy + 4), fill=accent)
    elif key == "thinking":
        for dx in (-6, 0, 6):
            draw.ellipse((bcx + dx - 1, bcy - 1, bcx + dx + 1, bcy + 1), fill=accent)
    elif key == "speaking":
        for dx, h in ((-6, 3), (-2, 6), (2, 6), (6, 3)):
            draw.rectangle((bcx + dx - 1, bcy - h, bcx + dx + 1, bcy + h), fill=accent)
    elif key == "ready":
        draw.line((bcx - 5, bcy, bcx - 1, bcy + 4), fill=accent, width=2)
        draw.line((bcx - 1, bcy + 4, bcx + 5, bcy - 4), fill=accent, width=2)
    elif key == "draft":
        draw.rectangle((bcx - 4, bcy - 5, bcx + 4, bcy + 5), outline=accent, width=2)
        draw.line((bcx - 2, bcy - 2, bcx + 2, bcy - 2), fill=accent, width=1)
        draw.line((bcx - 2, bcy + 1, bcx + 2, bcy + 1), fill=accent, width=1)
    else:  # idle
        draw.ellipse((bcx - 4, bcy - 4, bcx + 4, bcy + 4), fill=accent)


def _draw_sparkles(draw: Any) -> None:
    """Decorate the level_up frame with small starbursts around the edges."""
    sparkle_color = (255, 255, 200)
    for sx, sy, sr in ((20, 20, 2), (100, 30, 3), (25, 95, 2),
                       (95, 90, 2), (60, 8, 2), (105, 100, 2)):
        draw.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=sparkle_color)
        draw.line((sx - sr - 2, sy, sx + sr + 2, sy), fill=sparkle_color, width=1)
        draw.line((sx, sy - sr - 2, sx, sy + sr + 2), fill=sparkle_color, width=1)


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------


FakeMascotRenderer = MascotRenderer
