#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/live-endpoints.sh"

init_live_script "Job endpoint smoke"
require_live_command curl
require_live_command python3

PLATFORM_ROUTE="${PLATFORM_ROUTE:-oc1}"
REGIONAL_ROUTE="${REGIONAL_ROUTE:-sea}"
QUEUE="${QUEUE:-RANKED_SOLO_5x5}"
LADDER="${LADDER:-challenger}"
JOB_MATCH_COUNT="${JOB_MATCH_COUNT:-1}"
JOB_WAIT_FOR_COMPLETION="${JOB_WAIT_FOR_COMPLETION:-0}"
JOB_TIMEOUT_SECONDS="${JOB_TIMEOUT_SECONDS:-120}"
JOB_POLL_INTERVAL_SECONDS="${JOB_POLL_INTERVAL_SECONDS:-3}"

run_http \
  "jobs_start_ladder_ingestion" \
  "POST" \
  "${BASE_URL}/jobs/ingestion/ladder?platform_route=${PLATFORM_ROUTE}&regional_route=${REGIONAL_ROUTE}&queue=${QUEUE}&ladder=${LADDER}&match_count=${JOB_MATCH_COUNT}" \
  "202"

JOB_ID=""
if [[ -s "${HTTP_LAST_BODY_FILE}" ]]; then
  JOB_ID="$(json_field "${HTTP_LAST_BODY_FILE}" "job_id" 2>/dev/null || true)"
fi

if [[ -z "${JOB_ID}" ]]; then
  FAILURES=$((FAILURES + 1))
  log_error "Could not extract job_id from ${HTTP_LAST_BODY_FILE}"
  finish_live_script "Job endpoint smoke"
  exit $?
fi

log_info "Created job_id: ${JOB_ID}"

run_http \
  "jobs_get_status" \
  "GET" \
  "${BASE_URL}/jobs/${JOB_ID}" \
  "200"

run_http \
  "jobs_get_result_initial" \
  "GET" \
  "${BASE_URL}/jobs/${JOB_ID}/result" \
  "200,202"

initial_result_status="$(json_field "${HTTP_LAST_BODY_FILE}" "status" 2>/dev/null || true)"
if [[ "${initial_result_status}" == "failed" ]]; then
  FAILURES=$((FAILURES + 1))
  log_error "Job ${JOB_ID} failed before polling began. See ${HTTP_LAST_BODY_FILE} for details."
fi

if [[ "${JOB_WAIT_FOR_COMPLETION}" != "1" ]]; then
  log_warn "Not waiting for full job completion. Set JOB_WAIT_FOR_COMPLETION=1 to poll until succeeded or failed."
  finish_live_script "Job endpoint smoke"
  exit $?
fi

log_info "Polling job until terminal state or ${JOB_TIMEOUT_SECONDS}s timeout."
deadline=$((SECONDS + JOB_TIMEOUT_SECONDS))
poll_count=0
terminal_status=""

while [[ "${SECONDS}" -lt "${deadline}" ]]; do
  poll_count=$((poll_count + 1))
  sleep "${JOB_POLL_INTERVAL_SECONDS}"
  run_http \
    "jobs_poll_status_${poll_count}" \
    "GET" \
    "${BASE_URL}/jobs/${JOB_ID}" \
    "200"

  current_status="$(json_field "${HTTP_LAST_BODY_FILE}" "status" 2>/dev/null || true)"
  progress="$(json_field "${HTTP_LAST_BODY_FILE}" "progress" 2>/dev/null || true)"
  log_info "Job ${JOB_ID} status: ${current_status}; progress: ${progress}"

  if [[ "${current_status}" == "succeeded" || "${current_status}" == "failed" ]]; then
    terminal_status="${current_status}"
    break
  fi
done

if [[ -z "${terminal_status}" ]]; then
  FAILURES=$((FAILURES + 1))
  log_error "Job ${JOB_ID} did not finish before timeout."
  finish_live_script "Job endpoint smoke"
  exit $?
fi

run_http \
  "jobs_get_result_final" \
  "GET" \
  "${BASE_URL}/jobs/${JOB_ID}/result" \
  "200"

if [[ "${terminal_status}" == "failed" ]]; then
  FAILURES=$((FAILURES + 1))
  log_error "Job ${JOB_ID} reached failed state. See result body for error details."
else
  log_success "Job ${JOB_ID} completed successfully."
fi

finish_live_script "Job endpoint smoke"
