"""Tests for the soak test harness shell script.

These are static checks over ``scripts/soak.sh``. We do not execute the soak
runner here (it requires a live server and a multi-hour wall clock); instead
we assert the script is shaped correctly so a CI run cannot drift from the
contract documented in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOAK_SCRIPT = REPO_ROOT / "scripts" / "soak.sh"


@pytest.fixture(scope="module")
def soak_source() -> str:
    assert SOAK_SCRIPT.is_file(), f"soak script not found at {SOAK_SCRIPT}"
    return SOAK_SCRIPT.read_text(encoding="utf-8")


def test_soak_script_uses_strict_mode(soak_source: str) -> None:
    """First non-shebang, non-blank, non-comment line must enable strict mode."""
    lines = [ln for ln in soak_source.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines, "soak script has no executable content"
    assert lines[0].strip() == "set -euo pipefail", (
        f"expected strict mode header, got: {lines[0]!r}"
    )


def test_soak_script_quotes_variables(soak_source: str) -> None:
    """Word-splitting hazards: an expansion in a *command argument* position
    (i.e. NOT inside any "..." or '...' on the same line) must be quoted.

    We walk each line, track whether the cursor is inside a double-quoted or
    single-quoted region, and only flag expansions that occur in the
    "unquoted" region. Excluded by design:
      - ${!VAR} (indirect reference)
      - ${#VAR} (length)
      - ${ARR[@]} / ${ARR[*]} (array access)
      - ${VAR:-default} / ${VAR:=default} / ${VAR:?err} / ${VAR:+alt}
        (parameter expansion operators, only safe to test unquoted inside [[ ]])
      - Pure assignment lines `X=${Y}` (we only police *use* sites)
    """
    body_lines = soak_source.splitlines()[1:]
    code_lines = [ln for ln in body_lines if not ln.lstrip().startswith("#")]

    # Sanity: the script must use quoting somewhere, otherwise the rule is
    # vacuous.
    assert '"${' in soak_source, "script contains no quoted variable expansions"

    # Matches ${VAR} / $VAR that we want to police (uppercase, with optional
    # :-default style operator). Excludes ${!, ${#, ${ARR[@]}, ${ARR[*]}.
    expansion_re = re.compile(
        r"\$(?:\{([A-Z_][A-Z0-9_]*)(\:?[-=+?][^}]*)?\}|[A-Z_][A-Z0-9_]*)"
    )

    def find_unquoted_expansions(line: str) -> list[tuple[int, str]]:
        # Walk the line tracking in-string state. We do NOT handle every
        # quoting edge case (e.g. $'...', heredocs) — we only need to catch
        # the common word-splitting hazard.
        out: list[tuple[int, str]] = []
        i = 0
        n = len(line)
        in_dquote = False
        in_squote = False
        while i < n:
            ch = line[i]
            if in_squote:
                if ch == "'":
                    in_squote = False
                i += 1
                continue
            if in_dquote:
                if ch == '"':
                    in_dquote = False
                i += 1
                continue
            if ch == "'":
                in_squote = True
                i += 1
                continue
            if ch == '"':
                in_dquote = True
                i += 1
                continue
            if ch == "\\":
                # Skip escaped char.
                i += 2
                continue
            if ch == "$":
                m = expansion_re.match(line, i)
                if m:
                    out.append((i, m.group(0)))
                    i = m.end()
                    continue
            i += 1
        return out

    bad: list[str] = []
    for n, line in enumerate(code_lines, start=2):
        # Skip pure assignment lines (X=${Y} at the top of the statement).
        if re.match(r"^\s*[A-Z_][A-Z0-9_]*\s*=\s*", line):
            continue
        # Skip `[[ ... ]]` test contexts (word splitting is suppressed).
        if re.search(r"\[\[\s.*\s\]\]", line):
            continue
        # Skip `(( ... ))` arithmetic contexts.
        if re.search(r"\(\(\s.*\s\)\)", line):
            continue
        for pos, _tok in find_unquoted_expansions(line):
            bad.append(f"line {n} col {pos}: {line.strip()}")

    assert not bad, (
        "unquoted variable expansions in command position:\n" + "\n".join(bad)
    )


def test_soak_script_traps_signals(soak_source: str) -> None:
    """The script must install a cleanup trap and respond to SIGINT/SIGTERM."""
    assert "trap cleanup EXIT" in soak_source, "missing EXIT trap for cleanup"
    assert re.search(r"trap\s+['\"][^'\"]*INT", soak_source), "missing SIGINT handler"
    assert re.search(r"trap\s+['\"][^'\"]*TERM", soak_source), "missing SIGTERM handler"
    # The trap must be installed before the main work (before the main loop).
    trap_line = next(
        i for i, ln in enumerate(soak_source.splitlines()) if "trap cleanup EXIT" in ln
    )
    loop_marker = "while "
    loop_line = next(
        (i for i, ln in enumerate(soak_source.splitlines()) if loop_marker in ln),
        len(soak_source.splitlines()),
    )
    assert trap_line < loop_line, "trap must be installed before the main loop"


def test_soak_script_has_preflight(soak_source: str) -> None:
    """The pre-flight must hit /api/profile and fail fast on a non-2xx."""
    assert "curl --max-time 5 -fsS" in soak_source, "pre-flight curl missing --max-time 5 -fsS"
    assert "/api/profile" in soak_source, "pre-flight must probe /api/profile"
    # The pre-flight curl must come BEFORE the main loop.
    preflight_idx = soak_source.find("curl --max-time 5 -fsS")
    loop_idx = soak_source.find("while ")
    assert 0 <= preflight_idx < loop_idx, "pre-flight must run before the main loop"


def test_soak_script_writes_csv(soak_source: str) -> None:
    """A profile CSV (xp+level) and a memory CSV (rss,vsz,pcpu) must be written."""
    # Profile CSV with xp+level columns.
    assert re.search(r"PROFILE_CSV=.*profile\.csv", soak_source), (
        "PROFILE_CSV must point at a profile.csv file"
    )
    assert re.search(r"\bxp\b.*\blevel\b", soak_source), "CSV must record xp and level"
    assert ">> ${PROFILE_CSV}" in soak_source or '>> "${PROFILE_CSV}"' in soak_source, (
        "profile CSV must be appended to each tick"
    )

    # Memory CSV using ps.
    assert re.search(r"MEMORY_CSV=.*memory\.csv", soak_source), (
        "MEMORY_CSV must point at a memory.csv file"
    )
    assert "pgrep -f 'vibedump.app'" in soak_source, (
        "memory sampler must use pgrep -f 'vibedump.app'"
    )
    assert re.search(r"ps\s+-o\s+rss=?,\s*vsz=?,\s*pcpu=?", soak_source), (
        "memory sampler must use ps -o rss,vsz,pcpu"
    )


def test_soak_script_respects_duration_env(soak_source: str) -> None:
    """DURATION_HOURS must come from the environment with a default of 1."""
    m = re.search(r'DURATION_HOURS="\$\{DURATION_HOURS:-1\}"', soak_source)
    assert m, "DURATION_HOURS must default to 1 via ${DURATION_HOURS:-1}"
    # And the deadline computation must use it.
    assert "DURATION_HOURS" in soak_source.split("DEADLINE", 1)[0] or re.search(
        r"h\s*=\s*\"\${DURATION_HOURS", soak_source
    ), "deadline calculation must consume DURATION_HOURS"
    assert "DEADLINE" in soak_source, "script must compute a deadline from DURATION_HOURS"


def test_soak_script_prints_pass_fail(soak_source: str) -> None:
    """Final summary must print a clear `soak PASS` or `soak FAIL` line."""
    assert re.search(r'echo\s+["\']soak PASS["\']', soak_source), (
        "script must print a 'soak PASS' line"
    )
    assert re.search(r'echo\s+["\']soak FAIL["\']', soak_source), (
        "script must print a 'soak FAIL' line"
    )
    # The pass/fail decision must be driven by the final /api/profile poll.
    pass_idx = soak_source.find('echo "soak PASS"')
    fail_idx = soak_source.find('echo "soak FAIL"')
    poll_idx = soak_source.rfind("poll_profile")
    assert poll_idx > 0, "script must call poll_profile at the end"
    assert poll_idx < pass_idx or poll_idx < fail_idx, (
        "final poll_profile call must drive the pass/fail decision"
    )


def test_soak_script_logs_to_timestamped_file(soak_source: str) -> None:
    """The log file must include a UTC timestamp in its name."""
    # Accept either the literal `$(date -u +...)` substitution OR a precomputed
    # RUN_STAMP variable that itself was set from `$(date -u +%Y%m%dT%H%M%SZ)`.
    has_inline_stamp = bool(
        re.search(r"soak-\$\(date -u \+%Y%m%dT%H%M%SZ\)\.log", soak_source)
    )
    has_stamp_var = bool(
        re.search(
            r"RUN_STAMP=\"\$\(date -u \+%Y%m%dT%H%M%SZ\)\"", soak_source
        )
    ) and "soak-${RUN_STAMP}.log" in soak_source
    assert has_inline_stamp or has_stamp_var, (
        "log file must be named soak-<UTC-timestamp>.log "
        "(inline `$(date -u +...)` or precomputed RUN_STAMP)"
    )
    assert re.search(r"LOG_FILE=.*soak-.*\.log", soak_source), "LOG_FILE must be assigned"
    # The log directory must be created.
    assert "mkdir -p \"${LOG_DIR}\"" in soak_source, "LOG_DIR must be created with mkdir -p"
