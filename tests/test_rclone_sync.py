"""Tests for the rclone bridge module (M6-P1).

Covers the protocol surface, the fake in-memory bridge, the factory's
three ``prefer`` modes, and the real bridge's JSON output parsing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vibedump.integrations.rclone_sync import (
    DEFAULT_REMOTE,
    HOST_LAYOUT,
    REMOTE_ENV_VAR,
    FakeRcloneBridge,
    RealRcloneBridge,
    RcloneBridge,
    RcloneBridgeError,
    RcloneNotAvailable,
    SyncResult,
    make_rclone,
)


# ---------------------------------------------------------------------------
# 1. Protocol is runtime checkable
# ---------------------------------------------------------------------------


def test_protocol_is_runtime_checkable() -> None:
    fake = FakeRcloneBridge()
    assert isinstance(fake, RcloneBridge)
    # The protocol exposes the four required methods.
    for method in ("is_available", "sync", "list_remote", "version"):
        assert hasattr(fake, method)
        assert callable(getattr(fake, method))


# ---------------------------------------------------------------------------
# 2. FakeRcloneBridge.sync returns ok
# ---------------------------------------------------------------------------


def test_fake_sync_returns_ok(tmp_path: Path) -> None:
    bridge = FakeRcloneBridge()
    result = bridge.sync(tmp_path, "dumps")
    assert isinstance(result, SyncResult)
    assert result.ok is True
    assert result.error is None
    assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# 3. FakeRcloneBridge.sync records files
# ---------------------------------------------------------------------------


def test_fake_sync_records_files(tmp_path: Path) -> None:
    bridge = FakeRcloneBridge()
    bridge.sync(tmp_path, "dumps")
    bridge.sync(tmp_path, "dumps")
    bridge.sync(tmp_path, "backups")
    assert bridge.sync_calls == [
        (str(tmp_path), "dumps"),
        (str(tmp_path), "dumps"),
        (str(tmp_path), "backups"),
    ]
    # The fake appends one new file per sync() call to the same local key.
    listing = bridge.list_remote("ignored")
    assert str(tmp_path) in listing
    assert len(bridge._files[str(tmp_path)]) == 3
    # A different local dir, when seeded, lives in its own bucket.
    other = tmp_path / "backups"
    bridge.seed(other, ["b1.bin"])
    assert len(bridge._files[str(other)]) == 1


# ---------------------------------------------------------------------------
# 4. FakeRcloneBridge.list_remote returns keys
# ---------------------------------------------------------------------------


def test_fake_list_remote_returns_keys(tmp_path: Path) -> None:
    bridge = FakeRcloneBridge()
    a = tmp_path / "a"
    b = tmp_path / "b"
    bridge.seed(a, ["a1.bin", "a2.bin"])
    bridge.seed(b, ["b1.bin"])
    keys = bridge.list_remote("anything")
    assert keys == sorted([str(a), str(b)])


# ---------------------------------------------------------------------------
# 5. FakeRcloneBridge.version returns pinned string
# ---------------------------------------------------------------------------


def test_fake_version_returns_pinned_string() -> None:
    bridge = FakeRcloneBridge()
    version = bridge.version()
    assert version == "rclone v1.66.0-fake"
    assert bridge.PINNED_VERSION == "rclone v1.66.0-fake"


# ---------------------------------------------------------------------------
# 6. make_rclone(prefer="fake") returns FakeRcloneBridge
# ---------------------------------------------------------------------------


def test_make_rclone_fake_returns_fake() -> None:
    bridge = make_rclone(prefer="fake")
    assert isinstance(bridge, FakeRcloneBridge)


# ---------------------------------------------------------------------------
# 7. make_rclone(prefer="real") raises when rclone missing
# ---------------------------------------------------------------------------


def test_make_rclone_real_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil := __import__("shutil", fromlist=["which"]),
        "which",
        lambda cmd: None if cmd == "rclone" else f"/usr/bin/{cmd}",
    )
    with pytest.raises(RcloneNotAvailable):
        make_rclone(prefer="real")


# ---------------------------------------------------------------------------
# 8. make_rclone(prefer="auto") falls back to fake
# ---------------------------------------------------------------------------


def test_make_rclone_auto_falls_back_to_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil := __import__("shutil", fromlist=["which"]),
        "which",
        lambda cmd: None if cmd == "rclone" else f"/usr/bin/{cmd}",
    )
    bridge = make_rclone(prefer="auto")
    assert isinstance(bridge, FakeRcloneBridge)


# ---------------------------------------------------------------------------
# 9. RealRcloneBridge parses JSON output
# ---------------------------------------------------------------------------


_SAMPLE_RCLONE_LSJON = (
    '[{"Name":"dump-001.json","Size":1024,"IsDir":false},'
    '{"Name":"dump-002.json","Size":2048,"IsDir":false},'
    '{"Name":"subdir","Size":0,"IsDir":true}]'
)


def _completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        args=["rclone"],
    )


def test_real_bridge_parses_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pretend the binary is on PATH.
    monkeypatch.setattr(
        "shutil.which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "rclone" else None
    )
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["rclone", "version"]:
            return _completed(stdout="rclone v1.66.0\n")
        if args[:2] == ["rclone", "lsjson"]:
            return _completed(stdout=_SAMPLE_RCLONE_LSJON)
        # Default: success with empty JSON log.
        return _completed(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = RealRcloneBridge(remote="gdrive:")
    # version() should be parsed from the first line.
    assert bridge.version() == "rclone v1.66.0"
    # list_remote should hand back the Name fields.
    names = bridge.list_remote("dumps")
    assert names == ["dump-001.json", "dump-002.json", "subdir"]
    # sync() should run successfully and emit the documented args. The local
    # dir must exist for the bridge to call rclone at all.
    local = tmp_path / "src"
    local.mkdir()
    with patch("vibedump.integrations.rclone_sync.time") as fake_time:
        fake_time.monotonic.side_effect = [100.0, 100.5]
        result = bridge.sync(local, "dumps")
    assert result.ok is True
    assert result.duration_seconds == pytest.approx(0.5)
    # Last call was the sync invocation.
    last = calls[-1]
    assert last[0] == "rclone"
    assert last[1] == "sync"


def test_real_bridge_raises_when_binary_missing() -> None:
    with patch("shutil.which", return_value=None):
        with pytest.raises(RcloneNotAvailable):
            RealRcloneBridge()


def test_real_bridge_list_remote_raises_on_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "rclone" else None
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(stdout="not-json-at-all"),
    )
    bridge = RealRcloneBridge(remote="gdrive:")
    with pytest.raises(RcloneBridgeError):
        bridge.list_remote("dumps")


def test_real_bridge_sync_reports_missing_local(tmp_path: Path) -> None:
    # Bridge should not need the binary for a non-existent local dir.
    bridge = RealRcloneBridge.__new__(RealRcloneBridge)
    bridge._remote = "gdrive:"
    result = bridge.sync(tmp_path / "definitely-missing", "dumps")
    assert result.ok is False
    assert result.error is not None
    assert "does not exist" in result.error


# ---------------------------------------------------------------------------
# 10. SyncResult dataclass defaults
# ---------------------------------------------------------------------------


def test_sync_result_dataclass_defaults() -> None:
    # Only the four required fields are mandatory; ``error`` defaults to None.
    result = SyncResult(
        ok=True,
        bytes_transferred=4096,
        files_transferred=3,
        duration_seconds=1.25,
    )
    assert result.error is None
    # All four counters round-trip through the dataclass unchanged.
    assert result.ok is True
    assert result.bytes_transferred == 4096
    assert result.files_transferred == 3
    assert result.duration_seconds == 1.25

    # Frozen: mutating a field must raise.
    with pytest.raises(Exception):
        result.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


def test_host_layout_keys_are_complete() -> None:
    expected = {
        "dumps",
        "blueprints",
        "audio",
        "exports",
        "backups",
        "provider_config_redacted",
        "readme",
    }
    assert set(HOST_LAYOUT) == expected
    # The redacted config is always redacted in the filename.
    assert "redacted" in HOST_LAYOUT["provider_config_redacted"]


def test_default_remote_and_env_var() -> None:
    assert DEFAULT_REMOTE == "gdrive:"
    assert REMOTE_ENV_VAR == "VIBEDUMP_RCLONE_REMOTE"


def test_rclone_bridge_error_is_runtime_error() -> None:
    assert issubclass(RcloneBridgeError, RuntimeError)
    assert issubclass(RcloneNotAvailable, RuntimeError)
