"""Tests for the hardware smoke probe + factory wiring.

The probe is pure-stdlib and is exercised in two directions:

* Unit tests use ``monkeypatch`` + ``tmp_path`` to fake the /dev layout
  and ``shutil.which`` lookups so both pass and fail paths are
  exercised deterministically.
* Integration tests for the ``make_*("auto")`` factories confirm the
  probe short-circuits them to their fake implementations when the
  required hardware is missing.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

from vibedump.integrations import audio_capture, hardware_probe, pisugar, whisplay
from vibedump.integrations.audio_capture import FakeAudioCapture
from vibedump.integrations.hardware_probe import (
    OPTIONAL_BINS,
    REQUIRED_PATHS,
    ProbeResult,
    check_all,
    check_bins,
    check_paths,
    main,
    summary,
)
from vibedump.integrations.pisugar import FakePiSugarBridge
from vibedump.integrations.whisplay import FakeWhisplayBridge


# ---------------------------------------------------------------------------
# 1. check_paths honors the REQUIRED_PATHS constant
# ---------------------------------------------------------------------------


def test_probe_check_paths_when_all_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_i2c = tmp_path / "i2c-1"
    fake_spi = tmp_path / "spidev0.0"
    fake_i2c.touch()
    fake_spi.touch()
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(fake_i2c), "spi": str(fake_spi)},
    )
    results = check_paths()
    assert len(results) == 2
    by_name = {r.name: r for r in results}
    assert by_name["i2c"].available is True
    assert by_name["spi"].available is True
    assert by_name["i2c"].path == str(fake_i2c)
    assert by_name["spi"].path == str(fake_spi)


def test_probe_check_paths_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point REQUIRED_PATHS at non-existent files; tmp_path is real but
    # the targets inside it are not created.
    missing_i2c = tmp_path / "missing-i2c-1"
    missing_spi = tmp_path / "missing-spidev0.0"
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(missing_i2c), "spi": str(missing_spi)},
    )
    results = check_paths()
    assert len(results) == 2
    assert all(r.available is False for r in results)
    # detail field should call out that the path is missing
    for r in results:
        assert "missing" in r.detail


# ---------------------------------------------------------------------------
# 2. check_bins uses shutil.which under the hood
# ---------------------------------------------------------------------------


def test_probe_check_bins_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force shutil.which to return a fixed lookup table so we can
    # observe which names the probe asked about.
    table = {
        "rclone": "/usr/bin/rclone",
        "arecord": None,  # missing
        "aplay": "/usr/bin/aplay",
        "faster_whisper": None,
        "piper": None,
    }
    monkeypatch.setattr(shutil, "which", lambda name: table.get(name))

    results = check_bins()
    by_name = {r.name: r for r in results}
    assert by_name["rclone"].available is True
    assert by_name["rclone"].detail == "/usr/bin/rclone"
    assert by_name["arecord"].available is False
    assert by_name["arecord"].detail == "missing"
    assert by_name["faster_whisper"].available is False
    assert by_name["piper"].available is False
    # Result order must match OPTIONAL_BINS so callers can zip safely.
    assert [r.name for r in results] == list(OPTIONAL_BINS)


# ---------------------------------------------------------------------------
# 3. check_all combines both
# ---------------------------------------------------------------------------


def test_probe_check_all_returns_combined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_i2c = tmp_path / "i2c-1"
    fake_spi = tmp_path / "spidev0.0"
    fake_i2c.touch()
    fake_spi.touch()
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(fake_i2c), "spi": str(fake_spi)},
    )
    # Wipe bins so the only thing in play is the path branch.
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = check_all()
    assert set(result.keys()) >= {"paths", "bins", "available"}
    assert isinstance(result["paths"], list)
    assert isinstance(result["bins"], list)
    assert result["available"] is False
    # The "available" flag is True iff every path and bin is found.
    monkeypatch.setattr(
        shutil, "which", lambda _name: f"/usr/bin/{_name}"
    )
    result2 = check_all()
    assert result2["available"] is True


# ---------------------------------------------------------------------------
# 4. summary is a human-readable table
# ---------------------------------------------------------------------------


def test_probe_summary_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_i2c = tmp_path / "i2c-1"
    fake_spi = tmp_path / "spidev0.0"
    fake_i2c.touch()
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(fake_i2c), "spi": str(fake_spi)},
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = check_all()
    out = summary(result)

    # Section headers should be present.
    assert "PATHS" in out
    assert "BINS" in out
    # Every probed name appears somewhere in the rendered string.
    for name in REQUIRED_PATHS:
        assert name in out
    for name in OPTIONAL_BINS:
        assert name in out
    # The "available" column header is included for the human reader.
    assert "available" in out
    # The "detail" column header is also present.
    assert "detail" in out


# ---------------------------------------------------------------------------
# 5-7. main() exit-code contract
# ---------------------------------------------------------------------------


def test_probe_main_exits_0_when_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_i2c = tmp_path / "i2c-1"
    fake_spi = tmp_path / "spidev0.0"
    fake_i2c.touch()
    fake_spi.touch()
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(fake_i2c), "spi": str(fake_spi)},
    )
    monkeypatch.setattr(shutil, "which", lambda _name: f"/usr/bin/{_name}")
    # Swallow stdout/stderr so the test log stays clean.
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        rc = main()
    assert rc == 0
    assert "PATHS" in buf_out.getvalue()


def test_probe_main_exits_1_when_path_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_i2c = tmp_path / "missing-i2c-1"
    missing_spi = tmp_path / "missing-spidev0.0"
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(missing_i2c), "spi": str(missing_spi)},
    )
    # Even with every bin available, a missing path is still exit 1.
    monkeypatch.setattr(shutil, "which", lambda _name: f"/usr/bin/{_name}")
    buf_out = io.StringIO()
    with redirect_stdout(buf_out):
        rc = main()
    assert rc == 1


def test_probe_main_exits_2_when_only_bin_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_i2c = tmp_path / "i2c-1"
    fake_spi = tmp_path / "spidev0.0"
    fake_i2c.touch()
    fake_spi.touch()
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(fake_i2c), "spi": str(fake_spi)},
    )
    # Paths are fine, but every optional bin is missing.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    buf_out = io.StringIO()
    with redirect_stdout(buf_out):
        rc = main()
    assert rc == 2


# ---------------------------------------------------------------------------
# 8-10. Factory wiring: smoke gate short-circuits to fake when required
#        device / binary is missing.
# ---------------------------------------------------------------------------


def test_whisplay_auto_falls_back_to_fake_when_no_spi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /dev/spidev0.0 missing on the test box -> auto must short-circuit.
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(tmp_path / "i2c-1"), "spi": "/dev/spidev0.0"},
    )
    # Belt-and-braces: nuke the spidev module too so that even if the
    # gate is bypassed the real bridge still fails to import.
    monkeypatch.setitem(sys.modules, "spidev", None)
    bridge = whisplay.make_whisplay(prefer="auto")
    assert isinstance(bridge, FakeWhisplayBridge)


def test_pisugar_auto_falls_back_to_fake_when_no_i2c(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point REQUIRED_PATHS at a path that definitely does not exist
    # on the test box so the smoke gate fails as designed.
    missing_i2c = tmp_path / "missing-i2c-1"
    monkeypatch.setattr(
        hardware_probe,
        "REQUIRED_PATHS",
        {"i2c": str(missing_i2c), "spi": str(tmp_path / "spidev0.0")},
    )
    bridge = pisugar.make_pisugar(prefer="auto")
    assert isinstance(bridge, FakePiSugarBridge)


def test_audio_capture_auto_falls_back_to_fake_when_no_arecord(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force arecord to be invisible on PATH.
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "arecord" else f"/usr/bin/{name}"
    )
    cap = audio_capture.make_audio_capture(prefer="auto")
    assert isinstance(cap, FakeAudioCapture)


# ---------------------------------------------------------------------------
# Sanity: the dataclass shape is what the rest of the app depends on
# ---------------------------------------------------------------------------


def test_probe_result_dataclass_shape() -> None:
    pr = ProbeResult(name="x", available=True, detail="/dev/x", path="/dev/x")
    assert pr.name == "x"
    assert pr.available is True
    assert pr.detail == "/dev/x"
    assert pr.path == "/dev/x"
    # Frozen: attempting to mutate must raise.
    with pytest.raises(Exception):
        pr.name = "y"  # type: ignore[misc]


def test_required_paths_contains_expected_keys() -> None:
    # The factory wiring filters by name; renaming these keys would
    # silently disable the smoke gate.
    assert "spi" in REQUIRED_PATHS
    assert "i2c" in REQUIRED_PATHS
    assert REQUIRED_PATHS["spi"] == "/dev/spidev0.0"
    assert REQUIRED_PATHS["i2c"] == "/dev/i2c-1"


def test_optional_bins_contains_arecord() -> None:
    # Audio capture's smoke gate keys off the "arecord" name.
    assert "arecord" in OPTIONAL_BINS
