#!/usr/bin/env bash
# Launch the Vibe-Dump web dev server.
#
# Usage: ./scripts/run_dev.sh [PORT] [--reload]
#
# Default port: 8080 (matches the Whisplay chatbot default in the handover).
# Set VIBEDUMP_HOST / VIBEDUMP_PORT env vars to override.
set -euo pipefail

PORT="${1:-${VIBEDUMP_PORT:-8080}}"
HOST="${VIBEDUMP_HOST:-0.0.0.0}"
RELOAD_FLAG=""
if [[ "${2:-}" == "--reload" || "${VIBEDUMP_RELOAD:-}" == "1" ]]; then
  RELOAD_FLAG="--reload"
fi

cd "$(dirname "$0")/.."

# Install the web extra on demand if FastAPI / uvicorn are missing.
if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "[run_dev] installing web extra (fastapi + uvicorn)…"
  pip install --quiet --break-system-packages 'fastapi>=0.118' 'uvicorn>=0.30' || \
    pip install --quiet 'fastapi>=0.118' 'uvicorn>=0.30'
fi

exec python -m uvicorn vibedump.server:app --host "$HOST" --port "$PORT" $RELOAD_FLAG
