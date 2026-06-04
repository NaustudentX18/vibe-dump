#!/usr/bin/env bash
# Install Whisplay HAT prerequisites on a Raspberry Pi Zero 2 W.
#
# Idempotent: re-running this script on a fully configured system is a no-op.
# Requires: sudo (apt, modprobe, raspi-config, groupmod) and a Pi-family CPU.
#
# What this does:
#   1. Installs system packages (spidev, smbus, RPi.GPIO) via apt.
#   2. Enables I2C + SPI in raspi-config (only if not already enabled).
#   3. Adds the current user to the spi and gpio groups.
#   4. Loads the spi-dev and i2c-dev kernel modules (and on-boot via /etc/modules).
#   5. Installs Pillow (the only Python dep that isn't in apt).
#   6. Probes /dev/spidev0.0 to confirm the HAT is stacked.
#
# Run with:   sudo ./scripts/install_whisplay_prereqs.sh
# Re-running: safe; everything is checked before being changed.

set -euo pipefail

log()  { printf '[whisplay-setup] %s\n' "$*"; }
fail() { printf '[whisplay-setup][FATAL] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

if [[ "${EUID}" -ne 0 ]]; then
  fail "this script must be run as root (try: sudo $0)"
fi

if ! command -v apt-get >/dev/null 2>&1; then
  fail "apt-get not found; this script targets Debian/Raspberry Pi OS"
fi

if ! command -v raspi-config >/dev/null 2>&1; then
  log "raspi-config not found; assuming I2C/SPI are already enabled in /boot/firmware/config.txt"
fi

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------

log "ensuring apt packages are installed: python3-spidev python3-smbus python3-rpi.gpio python3-pil"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3-spidev \
  python3-smbus \
  python3-rpi.gpio \
  python3-pil \
  python3-pip

# ---------------------------------------------------------------------------
# 2. Enable I2C + SPI via raspi-config (idempotent)
# ---------------------------------------------------------------------------

if command -v raspi-config >/dev/null 2>&1; then
  if raspi-config nonint get_spi 2>/dev/null | grep -q '^1$'; then
    log "SPI already enabled in raspi-config"
  else
    log "enabling SPI via raspi-config"
    raspi-config nonint do_spi 0
  fi

  if raspi-config nonint get_i2c 2>/dev/null | grep -q '^1$'; then
    log "I2C already enabled in raspi-config"
  else
    log "enabling I2C via raspi-config"
    raspi-config nonint do_i2c 0
  fi
else
  log "raspi-config unavailable; ensure dtparam=spi=on and dtparam=i2c_arm=on are set in /boot/firmware/config.txt"
fi

# ---------------------------------------------------------------------------
# 3. Group membership
# ---------------------------------------------------------------------------

SUDO_USER_NAME="${SUDO_USER:-}"
if [[ -z "$SUDO_USER_NAME" || "$SUDO_USER_NAME" == "root" ]]; then
  log "no SUDO_USER detected; skipping group changes (run interactively via sudo to add your user to spi/gpio)"
else
  for grp in spi gpio; do
    if id -nG "$SUDO_USER_NAME" | tr ' ' '\n' | grep -qx "$grp"; then
      log "user $SUDO_USER_NAME is already in group $grp"
    else
      log "adding $SUDO_USER_NAME to group $grp"
      adduser "$SUDO_USER_NAME" "$grp" >/dev/null
    fi
  done
  log "reminder: $SUDO_USER_NAME must log out and back in for group changes to take effect"
fi

# ---------------------------------------------------------------------------
# 4. Kernel modules
# ---------------------------------------------------------------------------

ensure_module() {
  local mod="$1"
  if grep -qx "$mod" /etc/modules 2>/dev/null; then
    log "module $mod already listed in /etc/modules"
  else
    log "appending $mod to /etc/modules"
    echo "$mod" >> /etc/modules
  fi

  if lsmod | grep -qx "$mod"; then
    log "module $mod already loaded"
  else
    log "loading $mod"
    modprobe "$mod"
  fi
}

ensure_module spi-dev
ensure_module i2c-dev

# ---------------------------------------------------------------------------
# 5. Python deps
# ---------------------------------------------------------------------------

if python3 -c "import spidev, smbus2, RPi.GPIO, PIL" 2>/dev/null; then
  log "Python deps already importable: spidev, smbus2, RPi.GPIO, PIL"
else
  log "installing Python deps (spidev, smbus2, Pillow) via pip"
  pip3 install --quiet --break-system-packages spidev smbus2 Pillow \
    || pip3 install --quiet spidev smbus2 Pillow
fi

# ---------------------------------------------------------------------------
# 6. Probe the HAT
# ---------------------------------------------------------------------------

SPIDEV_PATH="/dev/spidev${WHISPLAY_SPI_BUS:-0}.${WHISPLAY_SPI_DEV:-0}"
if [[ -e "$SPIDEV_PATH" ]]; then
  log "Whisplay HAT detected at $SPIDEV_PATH"
else
  log "WARNING: $SPIDEV_PATH not present. A reboot may be required if SPI was just enabled."
fi

log "Whisplay prerequisite install complete."
log "Next steps:"
log "  1. Log out and back in (so the spi/gpio group additions take effect)."
log "  2. Optionally reboot: sudo reboot"
log "  3. Verify with: python -c 'from vibedump.integrations.whisplay import RealWhisplayBridge; RealWhisplayBridge()'"
