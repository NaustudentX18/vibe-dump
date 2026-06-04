"""rclone bridge for syncing local data dirs to a cloud remote.

Mirrors the protocol/factory pattern used by the other integrations
(``audio_capture``, ``pisugar``):

* ``RcloneBridge`` is the :class:`~typing.Protocol`.
* ``RealRcloneBridge`` shells out to the ``rclone`` CLI using a list of
  args (never ``shell=True``) and reads ``VIBEDUMP_RCLONE_REMOTE`` to
  pick the default remote.
* ``FakeRcloneBridge`` is an in-memory stand-in used by tests and
  fake-first dev runs.
* ``make_rclone(prefer=...)`` selects between the two.

The ``HOST_LAYOUT`` dict exposes the well-known subdirectory names
under ``$VIBEDUMP_DATA_DIR`` so the sync code and the docs can agree
on a single source of truth. ``provider_config_redacted`` is always
the redacted config artifact — never the raw provider config file.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Default remote name when ``VIBEDUMP_RCLONE_REMOTE`` is unset. ``gdrive:``
# is the canonical rclone path-style syntax for a Google Drive remote.
DEFAULT_REMOTE = "gdrive:"

# Environment variable consulted by ``RealRcloneBridge`` to pick the
# default remote. Operators may set this to e.g. ``b2:`` or ``s3:``.
REMOTE_ENV_VAR = "VIBEDUMP_RCLONE_REMOTE"

# Path segments (relative to ``$VIBEDUMP_DATA_DIR``) that the host
# knows about. The values are used by the sync driver to know which
# subdirs to mirror and which to keep purely local.
HOST_LAYOUT: dict[str, str] = {
    "dumps": "dumps",
    "blueprints": "blueprints",
    "audio": "audio",
    "exports": "exports",
    "backups": "backups",
    # Always the redacted artifact; never the raw provider config.
    "provider_config_redacted": "exports/provider_config.redacted.json",
    # Human-readable index synced alongside the artifacts.
    "readme": "README.md",
}


class RcloneNotAvailable(RuntimeError):
    """Raised when the real ``rclone`` binary cannot be located or used."""


class RcloneBridgeError(RuntimeError):
    """Raised when ``rclone`` output cannot be parsed or the call fails."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Outcome of a single ``rclone sync`` call."""

    ok: bool
    bytes_transferred: int
    files_transferred: int
    duration_seconds: float
    error: str | None = None


@runtime_checkable
class RcloneBridge(Protocol):
    """Minimal contract any rclone integration must satisfy."""

    def is_available(self) -> bool: ...

    def sync(self, local_dir: Path, remote_path: str) -> SyncResult: ...

    def list_remote(self, remote_path: str) -> list[str]: ...

    def version(self) -> str | None: ...


def _default_remote() -> str:
    """Return the configured default remote, falling back to ``gdrive:``."""
    import os

    return os.environ.get(REMOTE_ENV_VAR, DEFAULT_REMOTE)


class FakeRcloneBridge:
    """In-memory rclone bridge for tests and fake-first dev runs.

    The fake keeps a dict from a local path string to a list of file
    names. ``sync`` is a no-op that returns a deterministic
    :class:`SyncResult` and records the call. ``list_remote`` returns
    the keys of the dict. ``version`` returns a pinned string so
    callers can assert on it.
    """

    PINNED_VERSION = "rclone v1.66.0-fake"

    def __init__(self) -> None:
        self._files: dict[str, list[str]] = {}
        self._sync_calls: list[tuple[str, str]] = []

    # rclone bridge surface -------------------------------------------------

    def is_available(self) -> bool:
        return True

    def sync(self, local_dir: Path, remote_path: str) -> SyncResult:
        local_key = str(local_dir)
        files = self._files.setdefault(local_key, [])
        # Pretend the local dir produced one new file each call.
        files.append(f"{local_dir.name or 'sync'}.bin")
        self._sync_calls.append((local_key, remote_path))
        return SyncResult(
            ok=True,
            bytes_transferred=len(files) * 1024,
            files_transferred=len(files),
            duration_seconds=0.001,
            error=None,
        )

    def list_remote(self, remote_path: str) -> list[str]:
        # Listing is by local key — the fake has no remote state.
        return sorted(self._files.keys())

    def version(self) -> str:
        return self.PINNED_VERSION

    # Test affordances ------------------------------------------------------

    def seed(self, local_dir: Path, files: list[str]) -> None:
        """Pretend the given local dir already produced these files."""
        self._files[str(local_dir)] = list(files)

    @property
    def sync_calls(self) -> list[tuple[str, str]]:
        return list(self._sync_calls)


class RealRcloneBridge:
    """Real rclone bridge. Shells out to the ``rclone`` CLI.

    Uses ``subprocess.run([...], ...)`` with a list of args — never
    ``shell=True`` per project rules. Output is parsed as JSON when
    ``rclone`` was invoked with ``--json``.
    """

    def __init__(self, remote: str | None = None) -> None:
        if shutil.which("rclone") is None:
            raise RcloneNotAvailable("rclone binary not found on PATH")
        self._remote = remote or _default_remote()

    # rclone bridge surface -------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which("rclone") is not None

    def _run(self, args: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["rclone", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def version(self) -> str | None:
        result = self._run(["version"])
        if result.returncode != 0:
            return None
        # `rclone version` first line is "rclone v1.x.y".
        first_line = (result.stdout or "").splitlines()[0].strip()
        return first_line or None

    def list_remote(self, remote_path: str) -> list[str]:
        target = f"{self._remote.rstrip('/')}/{remote_path.lstrip('/')}"
        result = self._run(["lsjson", target])
        if result.returncode != 0:
            raise RcloneBridgeError(
                f"rclone lsjson failed for {target}: "
                f"{result.stderr.strip() or 'unknown error'}"
            )
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RcloneBridgeError(
                f"rclone lsjson returned non-JSON output: {exc}"
            ) from exc
        names: list[str] = []
        for entry in payload:
            if isinstance(entry, dict) and "Name" in entry:
                names.append(str(entry["Name"]))
        return names

    def sync(self, local_dir: Path, remote_path: str) -> SyncResult:
        local = Path(local_dir)
        if not local.exists():
            return SyncResult(
                ok=False,
                bytes_transferred=0,
                files_transferred=0,
                duration_seconds=0.0,
                error=f"local dir does not exist: {local}",
            )
        target = f"{self._remote.rstrip('/')}/{remote_path.lstrip('/')}"
        start = time.monotonic()
        # `--stats` keeps the human progress line out of stdout so
        # `--json` output is the only thing we have to parse.
        # `--use-json-log` + `--log-format=json` make log lines JSON.
        result = self._run(
            [
                "sync",
                str(local),
                target,
                "--stats",
                "0",
                "--use-json-log",
            ],
            timeout=3600.0,
        )
        duration = time.monotonic() - start
        if result.returncode != 0:
            return SyncResult(
                ok=False,
                bytes_transferred=0,
                files_transferred=0,
                duration_seconds=duration,
                error=(result.stderr or "rclone sync failed").strip(),
            )
        bytes_xfer, files_xfer = _summarize_log(result.stdout or "")
        return SyncResult(
            ok=True,
            bytes_transferred=bytes_xfer,
            files_transferred=files_xfer,
            duration_seconds=duration,
            error=None,
        )


def _summarize_log(stdout: str) -> tuple[int, int]:
    """Pull transfer totals out of ``rclone``'s JSON log stream.

    Each non-empty line is a JSON object; we sum the ``bytes`` and
    count distinct ``path`` values across the entries that look like
    transfers. On any parse failure we degrade to zeros rather than
    raising — the sync itself succeeded.
    """
    bytes_total = 0
    files: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        # Transfer log lines have msg == "Transferred" with stats nested.
        stats = entry.get("stats")
        if isinstance(stats, dict):
            bytes_total += int(stats.get("bytes", 0) or 0)
            continue
        if "path" in entry and entry.get("msg") in {"Copied", "Moved", "Uploaded"}:
            files.add(str(entry["path"]))
    return bytes_total, len(files)


def make_rclone(prefer: Literal["auto", "real", "fake"] = "auto") -> RcloneBridge:
    """Pick a bridge implementation.

    * ``"fake"`` — always return :class:`FakeRcloneBridge`.
    * ``"real"`` — require :class:`RealRcloneBridge`; raise
      :class:`RcloneNotAvailable` if ``rclone`` is missing.
    * ``"auto"`` (default) — try the real bridge, fall back to fake.
    """
    if prefer == "fake":
        return FakeRcloneBridge()
    if prefer == "real":
        if shutil.which("rclone") is None:
            raise RcloneNotAvailable("rclone binary not found on PATH")
        return RealRcloneBridge()
    if prefer == "auto":
        try:
            return RealRcloneBridge()
        except RcloneNotAvailable as exc:
            logger.info("Falling back to FakeRcloneBridge: %s", exc)
            return FakeRcloneBridge()
    raise ValueError(f"unknown rclone prefer mode: {prefer!r}")


__all__ = [
    "DEFAULT_REMOTE",
    "FakeRcloneBridge",
    "HOST_LAYOUT",
    "RealRcloneBridge",
    "REMOTE_ENV_VAR",
    "RcloneBridge",
    "RcloneBridgeError",
    "RcloneNotAvailable",
    "SyncResult",
    "make_rclone",
]
