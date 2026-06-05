#!/usr/bin/env bash
# install_systemd.sh - install / uninstall / status helper for VibeDump systemd units.
#
# Usage:
#   sudo scripts/install_systemd.sh install
#   sudo scripts/install_systemd.sh uninstall
#   scripts/install_systemd.sh status
#
# No external dependencies beyond bash + systemctl. All paths are quoted;
# globs are not used.

set -euo pipefail

REPO_DIR="/home/pi/vibe-dump"
UNIT_DIR="/etc/systemd/system"
UNIT_SRC_DIR="${REPO_DIR}/scripts/systemd"

UNITS=(
  "vibedump.service"
  "vibedump-whisplay.service"
  "vibedump-pisugar.service"
  "vibedump-agent.service"
)

# Minimal color helpers - degrade gracefully on non-tty.
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  C_GREEN="$(tput setaf 2 || true)"
  C_RED="$(tput setaf 1 || true)"
  C_YELLOW="$(tput setaf 3 || true)"
  C_RESET="$(tput sgr0 || true)"
else
  C_GREEN=""
  C_RED=""
  C_YELLOW=""
  C_RESET=""
fi

print_color() {
  local color="$1"
  local label="$2"
  local unit="$3"
  local value="$4"
  printf "  %s%-7s%s %-32s %s\n" "${color}" "${label}" "${C_RESET}" "${unit}" "${value}"
}

color_for_state() {
  local state="$1"
  case "${state}" in
    enabled|active) printf "%s" "${C_GREEN}" ;;
    disabled|inactive|failed) printf "%s" "${C_RED}" ;;
    static) printf "%s" "${C_YELLOW}" ;;
    *) printf "%s" "${C_RESET}" ;;
  esac
}

do_install() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "install requires root. Re-run with sudo." >&2
    exit 1
  fi

  echo "Installing Vibe-Dump systemd units to ${UNIT_DIR} ..."
  for unit in "${UNITS[@]}"; do
    local src="${UNIT_SRC_DIR}/${unit}"
    local dst="${UNIT_DIR}/${unit}"
    if [[ ! -f "${src}" ]]; then
      echo "Missing unit file: ${src}" >&2
      exit 1
    fi
    install -m 0644 "${src}" "${dst}"
    echo "  copied ${unit}"
  done

  systemctl daemon-reload
  systemctl enable vibedump.service
  systemctl enable vibedump-whisplay.service
  systemctl enable vibedump-pisugar.service
  systemctl enable vibedump-agent.service

  echo
  echo "Run: sudo systemctl start vibedump"
}

do_uninstall() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "uninstall requires root. Re-run with sudo." >&2
    exit 1
  fi

  echo "Uninstalling Vibe-Dump systemd units ..."
  for unit in "${UNITS[@]}"; do
    local path="${UNIT_DIR}/${unit}"
    # disable --now is best-effort: ignore failures (unit may not be loaded).
    systemctl disable --now "${unit}" 2>/dev/null || true
    if [[ -f "${path}" ]]; then
      rm -f "${path}"
      echo "  removed ${unit}"
    else
      echo "  ${unit} not installed, skipped"
    fi
    systemctl reset-failed "${unit}" 2>/dev/null || true
  done

  systemctl daemon-reload
}

do_status() {
  echo "Vibe-Dump systemd unit status:"
  for unit in "${UNITS[@]}"; do
    local enabled_state="unknown"
    local active_state="unknown"
    if systemctl cat "${unit}" >/dev/null 2>&1; then
      enabled_state="$(systemctl is-enabled "${unit}" 2>/dev/null || echo unknown)"
      active_state="$(systemctl is-active "${unit}" 2>/dev/null || echo unknown)"
    else
      enabled_state="not-installed"
      active_state="not-installed"
    fi
    print_color "$(color_for_state "${enabled_state}")" "enabled" "${unit}" "${enabled_state}"
    print_color "$(color_for_state "${active_state}")" "active"  "${unit}" "${active_state}"
  done
}

case "${1:-}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  *)
    echo "Usage: $0 {install|uninstall|status}" >&2
    exit 2
    ;;
esac
