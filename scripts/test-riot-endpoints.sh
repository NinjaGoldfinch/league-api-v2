#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/live-endpoints.sh"

init_live_script "Riot mirror endpoint smoke"
require_live_command curl
require_live_command python3

PLATFORM_ROUTE="${PLATFORM_ROUTE:-oc1}"
REGIONAL_ROUTE="${REGIONAL_ROUTE:-sea}"
QUEUE="${QUEUE:-RANKED_SOLO_5x5}"
TIER="${TIER:-DIAMOND}"
DIVISION="${DIVISION:-I}"
PAGE="${PAGE:-1}"
MATCH_START="${MATCH_START:-0}"
MATCH_COUNT="${MATCH_COUNT:-20}"

run_http \
  "openapi_schema" \
  "GET" \
  "${BASE_URL}/openapi.json" \
  "200"

run_http \
  "league_challenger" \
  "GET" \
  "${BASE_URL}/lol/league/v4/challengerleagues/by-queue/${QUEUE}?platform_route=${PLATFORM_ROUTE}" \
  "200"

run_http \
  "league_grandmaster" \
  "GET" \
  "${BASE_URL}/lol/league/v4/grandmasterleagues/by-queue/${QUEUE}?platform_route=${PLATFORM_ROUTE}" \
  "200"

run_http \
  "league_master" \
  "GET" \
  "${BASE_URL}/lol/league/v4/masterleagues/by-queue/${QUEUE}?platform_route=${PLATFORM_ROUTE}" \
  "200"

run_http \
  "league_ranked_page" \
  "GET" \
  "${BASE_URL}/lol/league/v4/entries/${QUEUE}/${TIER}/${DIVISION}?platform_route=${PLATFORM_ROUTE}&page=${PAGE}" \
  "200"

if [[ -n "${SAMPLE_PUUID:-}" ]]; then
  run_http \
    "match_ids_by_puuid" \
    "GET" \
    "${BASE_URL}/lol/match/v5/matches/by-puuid/${SAMPLE_PUUID}/ids?regional_route=${REGIONAL_ROUTE}&start=${MATCH_START}&count=${MATCH_COUNT}" \
    "200"

  run_http \
    "match_replays_by_puuid" \
    "GET" \
    "${BASE_URL}/lol/match/v5/matches/by-puuid/${SAMPLE_PUUID}/replays?regional_route=${REGIONAL_ROUTE}" \
    "200,404"

  run_http \
    "league_entries_by_puuid" \
    "GET" \
    "${BASE_URL}/lol/league/v4/entries/by-puuid/${SAMPLE_PUUID}?platform_route=${PLATFORM_ROUTE}" \
    "200"
else
  record_skip "Set SAMPLE_PUUID to exercise Match-V5 PUUID and League-V4 PUUID endpoints."
fi

if [[ -n "${SAMPLE_MATCH_ID:-}" ]]; then
  run_http \
    "match_detail" \
    "GET" \
    "${BASE_URL}/lol/match/v5/matches/${SAMPLE_MATCH_ID}?regional_route=${REGIONAL_ROUTE}" \
    "200"

  run_http \
    "match_timeline" \
    "GET" \
    "${BASE_URL}/lol/match/v5/matches/${SAMPLE_MATCH_ID}/timeline?regional_route=${REGIONAL_ROUTE}" \
    "200"
else
  record_skip "Set SAMPLE_MATCH_ID to exercise Match-V5 detail and timeline endpoints."
fi

finish_live_script "Riot mirror endpoint smoke"
