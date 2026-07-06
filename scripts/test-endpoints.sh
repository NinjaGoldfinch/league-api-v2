#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${LOG_DIR:-${TMPDIR:-/tmp}/league-api-v2-endpoint-logs/${RUN_ID}}"
RUN_RIOT_ENDPOINTS="${RUN_RIOT_ENDPOINTS:-1}"
RUN_JOB_ENDPOINTS="${RUN_JOB_ENDPOINTS:-1}"

export RUN_ID LOG_DIR

printf '[INFO] League API live endpoint suite\n'
printf '[INFO] Base URL: %s\n' "${BASE_URL:-http://localhost:8000}"
printf '[INFO] Log directory: %s\n' "${LOG_DIR}"
printf '[INFO] RUN_RIOT_ENDPOINTS=%s RUN_JOB_ENDPOINTS=%s\n' "${RUN_RIOT_ENDPOINTS}" "${RUN_JOB_ENDPOINTS}"

failures=0

run_script() {
  local label="$1"
  local script_path="$2"

  printf '\n[INFO] ===== %s =====\n' "${label}"
  if bash "${script_path}"; then
    printf '[SUCCESS] %s passed\n' "${label}"
  else
    failures=$((failures + 1))
    printf '[ERROR] %s failed\n' "${label}" >&2
  fi
}

if [[ "${RUN_RIOT_ENDPOINTS}" == "1" ]]; then
  run_script "Riot mirror endpoints" "${SCRIPT_DIR}/test-riot-endpoints.sh"
else
  printf '[WARN] Skipping Riot mirror endpoints because RUN_RIOT_ENDPOINTS=%s\n' "${RUN_RIOT_ENDPOINTS}"
fi

if [[ "${RUN_JOB_ENDPOINTS}" == "1" ]]; then
  run_script "Job endpoints" "${SCRIPT_DIR}/test-job-endpoints.sh"
else
  printf '[WARN] Skipping job endpoints because RUN_JOB_ENDPOINTS=%s\n' "${RUN_JOB_ENDPOINTS}"
fi

printf '\n[INFO] ===== Overall summary =====\n'
printf '[INFO] Logs saved under: %s\n' "${LOG_DIR}"

if [[ "${failures}" -gt 0 ]]; then
  printf '[ERROR] %s script group(s) failed\n' "${failures}" >&2
  exit 1
fi

printf '[SUCCESS] All requested endpoint script groups passed\n'
