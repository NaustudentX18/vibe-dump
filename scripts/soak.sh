#!/usr/bin/env bash
# Soak test harness for the Vibe-Dump FastAPI server.
#
# Drives a long-running synthetic load against a locally-running instance to
# surface memory leaks, CPU drift, and request failures. The script is read-only
# from the server's perspective: it only calls public HTTP routes.
#
# Usage:
#   DURATION_HOURS=0.05 ./scripts/soak.sh     # ~3 minutes
#   PORT=9000 ./scripts/soak.sh               # different port
#   BASE_URL=http://host:9000 ./scripts/soak.sh  # remote target
#
# Environment overrides (all optional):
#   DURATION_HOURS   total wall-clock duration (default: 1)
#   PORT             server port; used to derive BASE_URL (default: 8080)
#   BASE_URL         full base URL (overrides PORT if set)
#   LOG_DIR          directory for run logs + CSVs (default: ./soak-logs)
#   OUT_DIR          directory for final summary (default: ./soak-out)
#   TICK_SECONDS     interval between soak ticks (default: 30)
#   DUMP_COUNT       number of rotating dumps to keep alive (default: 5)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DURATION_HOURS="${DURATION_HOURS:-1}"
PORT="${PORT:-8080}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
LOG_DIR="${LOG_DIR:-./soak-logs}"
OUT_DIR="${OUT_DIR:-./soak-out}"
TICK_SECONDS="${TICK_SECONDS:-30}"
DUMP_COUNT="${DUMP_COUNT:-5}"

# Sanitize integer env vars (default if non-numeric, since `set -u` would
# otherwise reject an empty value).
for _v in DURATION_HOURS PORT TICK_SECONDS DUMP_COUNT; do
  if ! [[ "${!_v}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "[soak] ${_v} must be a positive number; got '${!_v}'" >&2
    exit 2
  fi
done

# ---------------------------------------------------------------------------
# Paths + logging
# ---------------------------------------------------------------------------
mkdir -p "${LOG_DIR}" "${OUT_DIR}"
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/soak-${RUN_STAMP}.log"
PROFILE_CSV="${LOG_DIR}/profile.csv"
MEMORY_CSV="${LOG_DIR}/memory.csv"
SUMMARY_FILE="${OUT_DIR}/summary-${RUN_STAMP}.txt"

# Best-effort JSON extractor. We try jq first (fast, ubiquitous in CI) and
# fall back to python3 so the script runs on minimal images.
json_get() {
  local path="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r "${path}"
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); p='${path}'.lstrip('.').split('.'); v=d
for k in p: v=v[int(k)] if k.isdigit() else v[k]
print(v if v is not None else '')"
  fi
}

log() {
  local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
  echo "${msg}" | tee -a "${LOG_FILE}"
}

# ---------------------------------------------------------------------------
# Cleanup trap (installed BEFORE any work so we always clean up).
# ---------------------------------------------------------------------------
TMP_FILES=()
CURL_PIDS=()

cleanup() {
  local rc=$?
  log "[soak] cleanup (rc=${rc})"
  for pid in "${CURL_PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
  for f in "${TMP_FILES[@]:-}"; do
    [[ -n "${f:-}" && -f "${f}" ]] && rm -f "${f}"
  done
  exit "${rc}"
}

on_signal() {
  local sig="$1"
  log "[soak] received ${sig}; finalising summary and exiting"
  # Disable the generic cleanup's exit and run summary directly.
  trap - EXIT
  print_summary
  cleanup 0
}

trap cleanup EXIT
trap 'on_signal SIGINT' INT
trap 'on_signal SIGTERM' TERM

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
TOTAL_DUMPS=0
TOTAL_TURNS=0
ERRORS=0
PEAK_RSS_KB=0
CPU_SAMPLES=()
CPU_SUM="0"
DUMP_IDS=()
TICK_INDEX=0

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
log "[soak] starting run=${RUN_STAMP} duration=${DURATION_HOURS}h base=${BASE_URL}"
log "[soak] log_file=${LOG_FILE}"

if ! curl --max-time 5 -fsS "${BASE_URL}/api/profile" >/dev/null 2>"${LOG_FILE}.preflight.err"; then
  log "[soak] FAIL: pre-flight /api/profile did not return 2xx"
  cat "${LOG_FILE}.preflight.err" >&2 || true
  exit 1
fi
rm -f "${LOG_FILE}.preflight.err"
log "[soak] pre-flight OK"

# Write CSV headers.
printf 'iso,xp,level\n' > "${PROFILE_CSV}"
printf 'iso,rss_kb,vsz_kb,pcpu\n' > "${MEMORY_CSV}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
END_EPOCH=$(awk -v h="${DURATION_HOURS}" 'BEGIN { printf "%d", h * 3600 }')
START_EPOCH=$(date +%s)
DEADLINE=$(( START_EPOCH + END_EPOCH ))

spawn_dump() {
  local title="$1"
  local body_file
  body_file="$(mktemp)"
  TMP_FILES+=("${body_file}")
  local http_code
  http_code=$(curl --max-time 10 -sS -o "${body_file}" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -X POST "${BASE_URL}/api/dumps" \
    --data "{\"title\": \"${title}\"}" || echo "000")
  if [[ "${http_code}" != "201" ]]; then
    ERRORS=$((ERRORS + 1))
    log "[soak] dump create failed http=${http_code} title=${title}"
    return 1
  fi
  local id
  id=$(json_get '.id' < "${body_file}" 2>/dev/null || echo "")
  if [[ -z "${id}" || "${id}" == "null" ]]; then
    ERRORS=$((ERRORS + 1))
    log "[soak] dump create returned no id for title=${title}"
    return 1
  fi
  TOTAL_DUMPS=$((TOTAL_DUMPS + 1))
  echo "${id}"
}

post_turn() {
  local dump_id="$1"
  local body_file
  body_file="$(mktemp)"
  TMP_FILES+=("${body_file}")
  local http_code
  http_code=$(curl --max-time 10 -sS -o "${body_file}" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -X POST "${BASE_URL}/api/dumps/${dump_id}/turn" \
    --data '{"text": "soak turn"}' || echo "000")
  if [[ "${http_code}" != "201" ]]; then
    ERRORS=$((ERRORS + 1))
    log "[soak] turn failed http=${http_code} dump_id=${dump_id}"
    return 1
  fi
  TOTAL_TURNS=$((TOTAL_TURNS + 1))
  return 0
}

poll_profile() {
  local body_file
  body_file="$(mktemp)"
  TMP_FILES+=("${body_file}")
  local http_code
  http_code=$(curl --max-time 5 -sS -o "${body_file}" -w '%{http_code}' \
    "${BASE_URL}/api/profile" || echo "000")
  if [[ "${http_code}" != "200" ]]; then
    ERRORS=$((ERRORS + 1))
    log "[soak] profile poll failed http=${http_code}"
    return 1
  fi
  local xp level
  xp=$(json_get '.xp' < "${body_file}" 2>/dev/null || echo "")
  level=$(json_get '.level' < "${body_file}" 2>/dev/null || echo "")
  if [[ -z "${xp}" || -z "${level}" ]]; then
    ERRORS=$((ERRORS + 1))
    log "[soak] profile parse failed xp='${xp}' level='${level}'"
    return 1
  fi
  printf '%s,%s,%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${xp}" "${level}" >> "${PROFILE_CSV}"
  PROFILE_LAST_XP="${xp}"
  PROFILE_LAST_LEVEL="${level}"
  return 0
}

sample_memory() {
  local pid
  pid=$(pgrep -f 'vibedump.app' | head -1 || true)
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  local line
  line=$(ps -o rss=,vsz=,pcpu= -p "${pid}" 2>/dev/null || true)
  if [[ -z "${line}" ]]; then
    return 0
  fi
  # rss is in KB (from `ps -o rss=`).
  local rss vsz pcpu
  rss=$(echo "${line}" | awk '{print $1}')
  vsz=$(echo "${line}" | awk '{print $2}')
  pcpu=$(echo "${line}" | awk '{print $3}')
  printf '%s,%s,%s,%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${rss}" "${vsz}" "${pcpu}" >> "${MEMORY_CSV}"
  if [[ "${rss}" =~ ^[0-9]+$ ]] && (( rss > PEAK_RSS_KB )); then
    PEAK_RSS_KB="${rss}"
  fi
  if [[ "${pcpu}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    CPU_SAMPLES+=("${pcpu}")
    CPU_SUM=$(awk -v a="${CPU_SUM}" -v b="${pcpu}" 'BEGIN { printf "%.4f", a + b }')
  fi
}

# ---------------------------------------------------------------------------
# Seed rotating dumps
# ---------------------------------------------------------------------------
log "[soak] seeding ${DUMP_COUNT} rotating dumps"
for i in $(seq 1 "${DUMP_COUNT}"); do
  id=$(spawn_dump "soak-seed-${i}-${RUN_STAMP}") || {
    log "[soak] failed to seed dump ${i}; aborting"
    exit 1
  }
  DUMP_IDS+=("${id}")
done
log "[soak] seeded dump ids: ${DUMP_IDS[*]}"

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
log "[soak] entering main loop; deadline epoch=${DEADLINE}"
while (( $(date +%s) < DEADLINE )); do
  # Pick the dump for this tick by rotating through the seed list.
  slot=$(( TICK_INDEX % DUMP_COUNT ))
  target_dump="${DUMP_IDS[${slot}]}"

  if ! post_turn "${target_dump}"; then
    # If a turn fails (e.g. dump got cleaned), try to respawn that slot.
    log "[soak] respawning dump at slot ${slot}"
    new_id=$(spawn_dump "soak-respawn-${slot}-${RUN_STAMP}") && DUMP_IDS[${slot}]="${new_id}"
  fi

  poll_profile || true
  sample_memory || true

  TICK_INDEX=$((TICK_INDEX + 1))

  # Sleep, but break out promptly if the deadline has passed.
  remaining=$(( DEADLINE - $(date +%s) ))
  if (( remaining <= 0 )); then
    break
  fi
  sleep_for=$(( remaining < TICK_SECONDS ? remaining : TICK_SECONDS ))
  sleep "${sleep_for}"
done

log "[soak] main loop complete after ${TICK_INDEX} ticks"

# ---------------------------------------------------------------------------
# Final profile read drives the PASS/FAIL verdict.
# ---------------------------------------------------------------------------
FINAL_OK=0
if poll_profile; then
  FINAL_OK=1
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
PEAK_RSS_MB=$(awk -v kb="${PEAK_RSS_KB}" 'BEGIN { printf "%.2f", kb / 1024 }')
if [[ "${#CPU_SAMPLES[@]}" -gt 0 ]]; then
  MEAN_CPU=$(awk -v s="${CPU_SUM}" -v n="${#CPU_SAMPLES[@]}" 'BEGIN { printf "%.2f", s / n }')
else
  MEAN_CPU="0.00"
fi

{
  echo "soak run summary (${RUN_STAMP})"
  echo "  base_url          : ${BASE_URL}"
  echo "  duration_hours    : ${DURATION_HOURS}"
  echo "  ticks             : ${TICK_INDEX}"
  echo "  total_dumps       : ${TOTAL_DUMPS}"
  echo "  total_turns       : ${TOTAL_TURNS}"
  echo "  final_xp          : ${PROFILE_LAST_XP:-n/a}"
  echo "  final_level       : ${PROFILE_LAST_LEVEL:-n/a}"
  echo "  peak_rss_mb       : ${PEAK_RSS_MB}"
  echo "  mean_cpu_pct      : ${MEAN_CPU}"
  echo "  errors            : ${ERRORS}"
  echo "  log_file          : ${LOG_FILE}"
  echo "  profile_csv       : ${PROFILE_CSV}"
  echo "  memory_csv        : ${MEMORY_CSV}"
} | tee "${SUMMARY_FILE}"

if (( FINAL_OK == 1 )); then
  echo "soak PASS"
  log "[soak] PASS"
  cleanup 0
else
  echo "soak FAIL"
  log "[soak] FAIL"
  cleanup 1
fi
