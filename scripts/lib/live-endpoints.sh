#!/usr/bin/env bash

set -u -o pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-30}"
RESPONSE_PREVIEW_CHARS="${RESPONSE_PREVIEW_CHARS:-1600}"
SHOW_RESPONSE_BODY="${SHOW_RESPONSE_BODY:-0}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${LOG_DIR:-${TMPDIR:-/tmp}/league-api-v2-endpoint-logs/${RUN_ID}}"

FAILURES=0
SUCCESSES=0
SKIPS=0
HTTP_LAST_BODY_FILE=""
HTTP_LAST_HEADERS_FILE=""
HTTP_LAST_STATUS=""

init_live_script() {
  local title="$1"
  mkdir -p "${LOG_DIR}"
  log_info "${title}"
  log_info "Base URL: ${BASE_URL}"
  log_info "Log directory: ${LOG_DIR}"
}

log_info() {
  printf '[INFO] %s\n' "$*"
}

log_success() {
  printf '[SUCCESS] %s\n' "$*"
}

log_warn() {
  printf '[WARN] %s\n' "$*"
}

log_error() {
  printf '[ERROR] %s\n' "$*" >&2
}

record_skip() {
  local message="$1"
  SKIPS=$((SKIPS + 1))
  log_warn "SKIP ${message}"
}

require_live_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    log_error "Required command not found: ${command_name}"
    FAILURES=$((FAILURES + 1))
    return 1
  fi
}

safe_name() {
  python3 - "$1" <<'PY'
import re
import sys

print(re.sub(r"[^A-Za-z0-9_.-]+", "_", sys.argv[1]).strip("_") or "request")
PY
}

url_path_segment() {
  python3 - "$1" <<'PY'
from urllib.parse import quote
import sys

print(quote(sys.argv[1], safe=""))
PY
}

contains_status() {
  local expected_csv="$1"
  local actual="$2"
  local IFS=","
  local expected

  for expected in ${expected_csv}; do
    if [[ "${expected}" == "${actual}" ]]; then
      return 0
    fi
  done
  return 1
}

run_http() {
  local name="$1"
  local method="$2"
  local url="$3"
  local expected_statuses="$4"
  shift 4

  local request_name
  request_name="$(safe_name "${name}")"
  local body_file="${LOG_DIR}/${request_name}.body.json"
  local headers_file="${LOG_DIR}/${request_name}.headers.txt"
  local error_file="${LOG_DIR}/${request_name}.curl-error.txt"
  local start_seconds
  local end_seconds
  local duration_seconds
  local curl_exit=0

  log_info "REQUEST ${name}: ${method} ${url}"
  start_seconds="$(date +%s)"
  HTTP_LAST_STATUS="$(
    curl \
      --silent \
      --show-error \
      --max-time "${HTTP_TIMEOUT}" \
      --request "${method}" \
      --dump-header "${headers_file}" \
      --output "${body_file}" \
      --write-out "%{http_code}" \
      "$@" \
      "${url}" 2>"${error_file}"
  )" || curl_exit=$?
  end_seconds="$(date +%s)"
  duration_seconds=$((end_seconds - start_seconds))

  HTTP_LAST_BODY_FILE="${body_file}"
  HTTP_LAST_HEADERS_FILE="${headers_file}"

  if [[ "${curl_exit}" -ne 0 ]]; then
    FAILURES=$((FAILURES + 1))
    log_error "FAIL ${name}: curl exited ${curl_exit} after ${duration_seconds}s"
    if [[ -s "${error_file}" ]]; then
      sed 's/^/[CURL] /' "${error_file}" >&2
    fi
    return 1
  fi

  if contains_status "${expected_statuses}" "${HTTP_LAST_STATUS}"; then
    SUCCESSES=$((SUCCESSES + 1))
    log_success "${name}: HTTP ${HTTP_LAST_STATUS} in ${duration_seconds}s"
  else
    FAILURES=$((FAILURES + 1))
    log_error "FAIL ${name}: expected HTTP ${expected_statuses}, got ${HTTP_LAST_STATUS}"
  fi

  summarize_response "${body_file}"
}

summarize_response() {
  local body_file="$1"

  if [[ ! -s "${body_file}" ]]; then
    log_info "Response body: empty"
    return 0
  fi

  python3 - "${body_file}" "${RESPONSE_PREVIEW_CHARS}" "${SHOW_RESPONSE_BODY}" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any

body_path = Path(sys.argv[1])
preview_chars = int(sys.argv[2])
show_body = sys.argv[3] == "1"
raw = body_path.read_text(encoding="utf-8", errors="replace")

try:
    payload: Any = json.loads(raw)
except json.JSONDecodeError:
    preview = raw[:preview_chars]
    print(f"[INFO] Response body is not JSON; preview: {preview}")
    print(f"[INFO] Full body: {body_path}")
    raise SystemExit(0)

if isinstance(payload, dict):
    keys = ", ".join(str(key) for key in list(payload)[:12])
    print(f"[INFO] JSON object keys: {keys}")
    for key in (
        "job_id",
        "job_type",
        "status",
        "message",
        "error",
        "progress",
        "summary",
        "player_puuids",
        "match_ids",
        "matches",
    ):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            print(f"[INFO]   {key}: list[{len(value)}] {value[:3]}")
        elif isinstance(value, dict):
            print(f"[INFO]   {key}: object[{len(value)}]")
        else:
            print(f"[INFO]   {key}: {value}")
elif isinstance(payload, list):
    print(f"[INFO] JSON list length: {len(payload)}")
    print(f"[INFO] First items: {payload[:3]}")
else:
    print(f"[INFO] JSON scalar: {payload}")

if show_body:
    formatted = json.dumps(payload, indent=2, sort_keys=True)
    print("[INFO] Response body:")
    print(formatted[:preview_chars])
    if len(formatted) > preview_chars:
        print(f"[INFO] Response body truncated at {preview_chars} characters.")

print(f"[INFO] Full body: {body_path}")
PY
}

json_field() {
  local body_file="$1"
  local field_name="$2"

  python3 - "${body_file}" "${field_name}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value = payload
for part in sys.argv[2].split("."):
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
        break

if value is None:
    raise SystemExit(1)
print(value)
PY
}

finish_live_script() {
  local title="$1"

  log_info "${title} summary: ${SUCCESSES} succeeded, ${SKIPS} skipped, ${FAILURES} failed"
  log_info "Logs saved under: ${LOG_DIR}"
  if [[ "${FAILURES}" -gt 0 ]]; then
    return 1
  fi
  return 0
}
