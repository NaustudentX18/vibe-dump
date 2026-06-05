"""Smoke tests that the doc set keeps the required sections.

These tests run in CI on every PR so the README and docs/ tree
cannot silently lose the sections that downstream users
(install scripts, hardware builders, future contributors) rely on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
ARCH = REPO_ROOT / "docs" / "ARCHITECTURE.md"
HARDWARE = REPO_ROOT / "docs" / "HARDWARE.md"
INSTALL = REPO_ROOT / "docs" / "INSTALL.md"
SCREENSHOTS = REPO_ROOT / "docs" / "screenshots"


def _read(path: Path) -> str:
    assert path.exists(), f"missing doc file: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------


def test_readme_has_hero() -> None:
    text = _read(README)
    assert "Vibe-Dump" in text
    assert "Voice dumps" in text and "Pi Zero 2 W" in text
    # The hero line is the very first non-heading content
    hero_line = text.splitlines()[2] if len(text.splitlines()) > 2 else ""
    assert "Voice dumps" in hero_line, f"hero line not on row 3: {hero_line!r}"


def test_readme_has_features() -> None:
    text = _read(README)
    # A real features heading followed by list items
    assert re.search(r"^##\s+Features\s*$", text, re.MULTILINE), "no Features heading"
    for needle in (
        "Voice capture",
        "Whisper",
        "Piper",
        "LLM",
        "SQLite",
        "mascot",
        "XP",
        "Whisplay",
        "PiSugar",
        "rclone",
    ):
        assert needle.lower() in text.lower(), f"features list missing: {needle!r}"


def test_readme_has_quickstart() -> None:
    text = _read(README)
    assert re.search(r"^##\s+Quickstart\s*$", text, re.MULTILINE), "no Quickstart heading"
    # Must include a curl one-liner with the placeholder marker
    assert "curl -fsSL" in text, "missing curl one-liner"
    assert "<placeholder>" in text, "placeholder marker must remain in the README"


def test_readme_has_hardware_bom() -> None:
    text = _read(README)
    assert re.search(r"^##\s+Hardware BOM\s*$", text, re.MULTILINE), "no BOM heading"
    for needle in ("Raspberry Pi Zero 2 W", "Whisplay", "PiSugar", "WM8960", "speaker"):
        assert needle.lower() in text.lower(), f"BOM missing: {needle!r}"


def test_readme_has_license() -> None:
    text = _read(README)
    assert re.search(r"^##\s+License\s*$", text, re.MULTILINE), "no License heading"
    assert "MIT" in text, "license must be MIT"


# ---------------------------------------------------------------------------
# ARCHITECTURE
# ---------------------------------------------------------------------------


def test_architecture_has_module_map() -> None:
    text = _read(ARCH)
    assert re.search(r"^##\s+Module map\s*$", text, re.MULTILINE), "no Module map heading"
    # A few canonical module paths must appear in the table
    for needle in (
        "vibedump.agent_pipeline",
        "vibedump.integrations.rclone_sync",
        "vibedump.database",
        "vibedump.events",
        "vibedump.providers",
        "vibedump.mascot_renderer",
    ):
        assert needle in text, f"module map missing: {needle}"


def test_architecture_has_state_machine() -> None:
    text = _read(ARCH)
    assert re.search(r"^##\s+State machine\s*$", text, re.MULTILINE), (
        "no State machine heading"
    )
    # The four-state transition must be present
    for state in ("draft", "listening", "thinking", "ready"):
        assert state in text, f"state machine missing state: {state!r}"


def test_architecture_has_milestone_table() -> None:
    text = _read(ARCH)
    assert re.search(r"^##\s+Milestone status\s*$", text, re.MULTILINE), (
        "no Milestone status heading"
    )
    # M0 through M9 must all appear in the table
    for m in (f"M{i}" for i in range(10)):
        assert m in text, f"milestone table missing: {m}"
    assert "M9" in text and ("openlaude" in text.lower() or "agent runtime" in text.lower()), (
        "M9 must mention the agent runtime / openlaude hook"
    )


# ---------------------------------------------------------------------------
# HARDWARE
# ---------------------------------------------------------------------------


def test_hardware_has_pin_map() -> None:
    text = _read(HARDWARE)
    assert re.search(r"^##\s+Whisplay HAT pin map\s*$", text, re.MULTILINE), (
        "no Whisplay HAT pin map heading"
    )
    # Required pin map entries
    for needle in ("SPI0 CE0", "GPIO13", "GPIO24", "ST7789", "WS2812", "WM8960"):
        assert needle in text, f"pin map missing: {needle!r}"


def test_hardware_has_pisugar_address() -> None:
    text = _read(HARDWARE)
    assert re.search(r"^##\s+PiSugar 3", text, re.MULTILINE), "no PiSugar 3 heading"
    assert "0x57" in text, "PiSugar I2C address 0x57 must be documented"
    assert "I2C" in text, "PiSugar section must mention I2C"


# ---------------------------------------------------------------------------
# INSTALL
# ---------------------------------------------------------------------------


def test_install_has_quickstart() -> None:
    text = _read(INSTALL)
    # A one-liner section that contains a curl placeholder
    assert "curl -fsSL" in text, "missing curl one-liner"
    assert "<placeholder>" in text, "placeholder marker must remain in INSTALL"
    # A manual path section that lists clone + venv + pip + systemd
    for needle in ("git clone", "python -m venv", "pip install -e", "install_systemd.sh"):
        assert needle in text, f"manual install missing step: {needle!r}"


def test_install_has_verification() -> None:
    text = _read(INSTALL)
    assert re.search(r"^##\s+Verify", text, re.MULTILINE), "no verification heading"
    assert "curl http://127.0.0.1:8080/api/profile" in text, (
        "verification must show the /api/profile probe"
    )
    assert "/api/health" in text, "verification must list additional probes"


# ---------------------------------------------------------------------------
# Screenshot placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "dumpi-idle.png",
        "dumpi-listening.png",
        "dumpi-blueprint.png",
        "dashboard-mobile.png",
        "dashboard-settings.png",
        "storage-panel.png",
    ],
)
def test_screenshot_placeholders_exist(filename: str) -> None:
    path = SCREENSHOTS / filename
    assert path.exists(), f"missing screenshot placeholder: {path}"
    # Placeholders carry a 1-byte body so Git tracks them
    assert path.stat().st_size >= 1, f"placeholder is empty: {path}"
