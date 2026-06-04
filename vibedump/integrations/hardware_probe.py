"""Hardware smoke probe for the Vibe-Dump Pocket Pi build.

This module is the single source of truth for "do we have the hardware
needed to talk to the real bridges?". It is a pure-stdlib utility used
by:

* :mod:`scripts.hardware_smoke` (operator-facing smoke check)
* the ``make_*("auto")`` factories in
  :mod:`vibedump.integrations.whisplay`,
  :mod:`vibedump.integrations.pisugar`, and
  :mod:`vibedump.integrations.audio_capture`
  (which short-circuit to their fake implementations when the required
  device / binary is missing).

The probe is intentionally tiny so it is safe to import from anywhere,
including the bridge modules themselves. There is no I/O beyond ``stat``
and a ``shutil.which`` lookup.

Exit-code contract (used by ``scripts/hardware_smoke.sh``):

* ``0`` - all required paths AND all optional bins are available
* ``1`` - at least one required path is missing (hardware is degraded)
* ``2`` - all required paths are present, but at least one optional
  binary is missing (software-only degraded; e.g. faster_whisper not
  installed on a laptop)
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Device nodes the canonical Pi build expects to see. Anything missing
#: here is a hardware failure, not a software-degradation, so it bumps
#: the smoke exit code to 1.
REQUIRED_PATHS: dict[str, str] = {
    "i2c": "/dev/i2c-1",
    "spi": "/dev/spidev0.0",
}

#: Binaries the rest of the app may or may not find on PATH. Missing
#: ones only bump the exit code to 2 because the app can still run in a
#: degraded mode (no STT, no TTS, no audio capture, no remote sync).
OPTIONAL_BINS: tuple[str, ...] = (
    "rclone",
    "arecord",
    "aplay",
    "faster_whisper",
    "piper",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One observation from the smoke probe.

    Attributes
    ----------
    name:
        Symbolic name of the resource ("i2c", "arecord", ...). Stable
        string suitable for log lines and dashboard labels.
    available:
        True if the resource is currently usable.
    detail:
        Human-readable explanation. For an available path this is the
        resolved path; for a missing one it says ``"missing"`` plus
        whatever extra context the lookup yielded.
    path:
        The filesystem path or binary name the probe looked at. ``None``
        only in the (currently unused) case where a future probe has no
        concrete target to report.
    """

    name: str
    available: bool
    detail: str
    path: str | None


# ---------------------------------------------------------------------------
# Probe functions
# ---------------------------------------------------------------------------


def _check_one_path(name: str, path: str) -> ProbeResult:
    """Stat a single required path and return a :class:`ProbeResult`.

    Uses :func:`os.path.exists` rather than :func:`os.stat` directly so
    tests can monkeypatch the lookup without juggling :class:`OSError`.
    """
    if os.path.exists(path):
        return ProbeResult(name=name, available=True, detail=path, path=path)
    return ProbeResult(
        name=name,
        available=False,
        detail=f"missing: {path}",
        path=path,
    )


def check_paths() -> list[ProbeResult]:
    """Probe every entry in :data:`REQUIRED_PATHS`.

    Returns one :class:`ProbeResult` per entry, in the dict's insertion
    order, so callers can zip the result against :data:`REQUIRED_PATHS`
    if they need a ``(name, result)`` view.
    """
    return [_check_one_path(name, path) for name, path in REQUIRED_PATHS.items()]


def check_bins() -> list[ProbeResult]:
    """Probe every entry in :data:`OPTIONAL_BINS` via ``shutil.which``.

    ``shutil.which`` is the canonical stdlib way to ask "is this on
    PATH?" and respects the current ``PATH`` plus ``PATHEXT`` on
    Windows. We accept the result as the authoritative "is this
    executable reachable" answer.
    """
    results: list[ProbeResult] = []
    for binary in OPTIONAL_BINS:
        resolved = shutil.which(binary)
        if resolved is None:
            results.append(
                ProbeResult(
                    name=binary,
                    available=False,
                    detail="missing",
                    path=binary,
                )
            )
        else:
            results.append(
                ProbeResult(
                    name=binary,
                    available=True,
                    detail=resolved,
                    path=binary,
                )
            )
    return results


def check_all() -> dict[str, object]:
    """Run every probe and return a combined result.

    The returned mapping has three keys:

    * ``"paths"`` - :class:`list` of :class:`ProbeResult` for the
      required device nodes.
    * ``"bins"``  - :class:`list` of :class:`ProbeResult` for the
      optional binaries.
    * ``"available"`` - :class:`bool` that is True iff every entry in
      both lists reports ``available=True``.

    The shape stays dict-shaped (rather than a custom dataclass) so the
    smoke script and the factories can both consume it without
    importing probe-internal types.
    """
    path_results = check_paths()
    bin_results = check_bins()
    all_ok = all(r.available for r in path_results) and all(
        r.available for r in bin_results
    )
    return {
        "paths": path_results,
        "bins": bin_results,
        "available": all_ok,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _render_rows(rows: list[ProbeResult]) -> list[str]:
    """Render a list of probe results as aligned ``|``-separated rows."""
    headers = ("name", "available", "detail")
    table_rows: list[tuple[str, str, str]] = [
        (r.name, "yes" if r.available else "no", r.detail) for r in rows
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in table_rows))
        for i in range(3)
    ]
    sep = "-+-".join("-" * w for w in widths)
    out: list[str] = []
    out.append(" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    out.append(sep)
    for row in table_rows:
        out.append(" | ".join(row[i].ljust(widths[i]) for i in range(3)))
    return out


def summary(result: dict[str, object]) -> str:
    """Render a :func:`check_all` result as a human-readable table.

    Two tables are emitted back-to-back: one for paths (required
    hardware) and one for bins (optional software). The function is
    pure formatting and never mutates ``result``.
    """
    path_rows = result.get("paths", [])  # type: ignore[arg-type]
    bin_rows = result.get("bins", [])  # type: ignore[arg-type]
    if not isinstance(path_rows, list):
        path_rows = []
    if not isinstance(bin_rows, list):
        bin_rows = []
    lines: list[str] = []
    lines.append("PATHS (required)")
    lines.extend(_render_rows(path_rows))
    lines.append("")
    lines.append("BINS (optional)")
    lines.extend(_render_rows(bin_rows))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _classify_exit(result: dict[str, object]) -> int:
    """Map a :func:`check_all` result to the documented exit code."""
    path_rows = result.get("paths", [])  # type: ignore[arg-type]
    bin_rows = result.get("bins", [])  # type: ignore[arg-type]
    if not isinstance(path_rows, list):
        path_rows = []
    if not isinstance(bin_rows, list):
        bin_rows = []
    paths_ok = all(r.available for r in path_rows)  # type: ignore[attr-defined]
    bins_ok = all(r.available for r in bin_rows)  # type: ignore[attr-defined]
    if paths_ok and bins_ok:
        return 0
    if not paths_ok:
        return 1
    return 2


def main() -> int:
    """Print the smoke summary to stdout and return the exit code.

    Designed to be invoked as ``python -m vibedump.integrations.hardware_probe``
    from :file:`scripts/hardware_smoke.sh`. The exit code is the only
    machine-readable channel; the printed table is for humans.
    """
    result = check_all()
    print(summary(result))
    return _classify_exit(result)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "OPTIONAL_BINS",
    "REQUIRED_PATHS",
    "ProbeResult",
    "check_all",
    "check_bins",
    "check_paths",
    "main",
    "summary",
]
