#!/usr/bin/env bash
# Vibe-Dump one-line installer (dev + Pi).
set -euo pipefail

REPO_URL="${VIBEDUMP_REPO_URL:-https://github.com/NaustudentX18/vibe-dump.git}"
INSTALL_DIR="${VIBEDUMP_INSTALL_DIR:-$HOME/vibe-dump}"

log() { printf '[vibedump-install] %s\n' "$*"; }

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  log "cloning $REPO_URL -> $INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
else
  log "updating existing clone at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
fi

cd "$INSTALL_DIR"

if [[ ! -d .venv ]]; then
  log "creating virtualenv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -U pip
pip install -q -e ".[web,all]"
pip install -q pillow

if [[ ! -f .env ]]; then
  cp .env.example .env
  log "created .env from .env.example"
fi

if [[ -f /proc/device-tree/model ]] && grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
  log "Pi detected — optional Whisplay driver install"
  if [[ -x scripts/install_whisplay_prereqs.sh ]]; then
    sudo scripts/install_whisplay_prereqs.sh || log "Whisplay prereqs skipped (run manually with sudo)"
  fi
fi

log "install complete"
log "  source .venv/bin/activate && ./scripts/run_dev.sh"
log "  dashboard: http://127.0.0.1:8080"
