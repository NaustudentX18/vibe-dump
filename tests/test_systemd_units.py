"""Static validation of the M8-P1 systemd units and installer.

The unit files and the bash installer are pure text, so this test module
checks them without touching the real systemd. We only assert structural
properties that are easy to verify and unlikely to drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = REPO_ROOT / "scripts" / "systemd"
INSTALLER = REPO_ROOT / "scripts" / "install_systemd.sh"

UNIT_FILES = {
    "vibedump": UNIT_DIR / "vibedump.service",
    "whisplay": UNIT_DIR / "vibedump-whisplay.service",
    "pisugar": UNIT_DIR / "vibedump-pisugar.service",
}


def _parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Minimal systemd-unit parser used only for the assertions below.

    Returns a dict mapping section name -> key -> ordered list of values.
    Lines like ``Key=val`` populate the list with a single entry; lines
    like ``Key=val1 val2`` would still be a single entry. Duplicates of
    the same key are preserved so we can detect repeated directives.
    """
    sections: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        section = re.match(r"\[(?P<name>[^\]]+)\]", line)
        if section:
            current = section.group("name")
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current].setdefault(key.strip(), []).append(value.strip())
    return sections


@pytest.fixture(scope="module")
def units() -> dict[str, dict[str, dict[str, list[str]]]]:
    parsed: dict[str, dict[str, dict[str, list[str]]]] = {}
    for kind, path in UNIT_FILES.items():
        assert path.exists(), f"unit file missing: {path}"
        parsed[kind] = _parse_unit(path.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def installer() -> str:
    assert INSTALLER.exists(), f"installer missing: {INSTALLER}"
    return INSTALLER.read_text(encoding="utf-8")


def test_vibedump_service_declares_required_keys(units) -> None:
    svc = units["vibedump"]
    for section, key in [
        ("Service", "Type"),
        ("Service", "ExecStart"),
        ("Service", "WorkingDirectory"),
        ("Service", "Restart"),
        ("Service", "RestartSec"),
        ("Service", "User"),
        ("Service", "Group"),
        ("Service", "EnvironmentFile"),
        ("Service", "Environment"),
        ("Unit", "Wants"),
        ("Unit", "After"),
        ("Install", "WantedBy"),
    ]:
        assert key in svc.get(section, {}), f"vibedump.service missing [{section}] {key}"
    # ExecStart should launch the web dashboard.
    assert "-m vibedump.app" in svc["Service"]["ExecStart"][0]
    assert "--port 8080" in svc["Service"]["ExecStart"][0]
    # WorkingDirectory must point at the repo root.
    assert svc["Service"]["WorkingDirectory"][0] == "/home/pi/vibe-dump"
    # VIBEDUMP_REGISTRY=real must be one of the Environment entries.
    env_values = svc["Service"]["Environment"]
    assert any(v == "VIBEDUMP_REGISTRY=real" for v in env_values), env_values


def test_vibedump_service_uses_environment_file(units) -> None:
    env_file = units["vibedump"]["Service"]["EnvironmentFile"]
    assert env_file == ["-/home/pi/vibe-dump/.env"], env_file
    # Re-read raw text to double-check the leading "-" is preserved
    # (the parser treats it as a value prefix but should still capture it).
    raw = UNIT_FILES["vibedump"].read_text(encoding="utf-8")
    assert re.search(r"^EnvironmentFile=-/home/pi/vibe-dump/\.env\s*$", raw, re.MULTILINE), raw


def test_whisplay_service_has_spi_condition(units) -> None:
    raw = UNIT_FILES["whisplay"].read_text(encoding="utf-8")
    assert re.search(r"^ConditionPathExists=/dev/spidev0\.0\s*$", raw, re.MULTILINE), raw
    svc = units["whisplay"]
    assert "vibedump.service" in svc["Unit"]["Wants"]
    assert "vibedump.service" in svc["Unit"]["After"]
    assert "vibedump.integrations.whisplay_daemon" in svc["Service"]["ExecStart"][0]


def test_pisugar_service_has_i2c_condition(units) -> None:
    raw = UNIT_FILES["pisugar"].read_text(encoding="utf-8")
    assert re.search(r"^ConditionPathExists=/dev/i2c-1\s*$", raw, re.MULTILINE), raw
    svc = units["pisugar"]
    assert "vibedump.service" in svc["Unit"]["Wants"]
    assert "vibedump.service" in svc["Unit"]["After"]
    assert "vibedump.integrations.pisugar_monitor" in svc["Service"]["ExecStart"][0]


def test_all_units_have_wantedby_multi_user(units) -> None:
    for kind, parsed in units.items():
        install = parsed.get("Install", {})
        assert "WantedBy" in install, f"{kind}.service missing [Install] WantedBy"
        assert "multi-user.target" in install["WantedBy"], install["WantedBy"]


def test_all_units_have_restart_on_failure(units) -> None:
    for kind, parsed in units.items():
        service = parsed.get("Service", {})
        assert service.get("Type") == ["simple"], f"{kind} Type must be simple"
        assert service.get("Restart") == ["on-failure"], f"{kind} Restart must be on-failure"
        assert service.get("RestartSec") == ["5s"], f"{kind} RestartSec must be 5s"
        assert service.get("User") == ["pi"], f"{kind} User must be pi"
        assert service.get("Group") == ["pi"], f"{kind} Group must be pi"


def test_install_script_has_subcommand_routing(installer: str) -> None:
    # The case dispatcher must include all three required subcommands.
    case_block = re.search(r'case\s+"\$\{1:-?\}".*?esac', installer, re.DOTALL)
    assert case_block, "no case dispatcher found in installer"
    body = case_block.group(0)
    for directive in ("install", "uninstall", "status"):
        assert re.search(rf'\b{directive}\b', body), f"missing subcommand: {directive}"
    # set -euo pipefail must be enabled at the top.
    assert re.search(r"^set -euo pipefail\s*$", installer, re.MULTILINE), installer


def test_install_script_quotes_paths(installer: str) -> None:
    # No unquoted globs, no [ ] (use [[ ]]), and no eval.
    assert "eval " not in installer, "eval is forbidden in the installer"
    # Disallow single-bracket tests; require [[.
    # The regex must not match characters that are part of a ${...} expansion
    # (e.g. ${UNITS[@]} is a valid bash array expansion, not a [ ] test).
    for match in re.finditer(r"(?<!\[)\[([^\[\]]*)\](?!\])", installer):
        # Walk backwards to see if this '[' is inside a ${...} block.
        start = match.start()
        prefix = installer[:start]
        last_open = prefix.rfind("${")
        last_close = prefix.rfind("}")
        if last_open != -1 and (last_close == -1 or last_open > last_close):
            continue  # Inside a parameter expansion, not a test.
        raise AssertionError(f"single-bracket test detected: {match.group(0)!r}")
    # Every variable expansion that flows into a path should be quoted.
    # We spot-check the two paths we know are used.
    assert 'UNIT_DIR="/etc/systemd/system"' in installer
    assert 'UNIT_SRC_DIR="${REPO_DIR}/scripts/systemd"' in installer
    # The unit list must be a bash array (not a glob).
    assert "UNITS=(" in installer


def test_uninstall_runs_disable_then_remove(installer: str) -> None:
    # Pull out the do_uninstall function body and assert ordering.
    fn = re.search(r"do_uninstall\(\)\s*\{(.*?)^\}", installer, re.MULTILINE | re.DOTALL)
    assert fn, "do_uninstall() not found"
    body = fn.group(1)
    disable_pos = body.find("systemctl disable --now")
    remove_pos = body.find("rm -f")
    reload_pos = body.find("daemon-reload")
    reset_pos = body.find("reset-failed")
    assert -1 < disable_pos < remove_pos < reload_pos, (
        f"expected disable < remove < daemon-reload, got "
        f"disable={disable_pos} remove={remove_pos} reload={reload_pos}"
    )
    assert reset_pos != -1, "reset-failed must be called during uninstall"
    # reset-failed should appear per-unit (inside the loop).
    assert body.count("reset-failed") >= 1
