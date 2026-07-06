#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/live-endpoints.sh"

init_live_script "Riot mirror endpoint smoke"
require_live_command curl
require_live_command python3

PLATFORM_ROUTE="${PLATFORM_ROUTE:-oc1}"
REGIONAL_ROUTE="${REGIONAL_ROUTE:-sea}"
ACCOUNT_REGIONAL_ROUTE="${ACCOUNT_REGIONAL_ROUTE:-asia}"
QUEUE="${QUEUE:-RANKED_SOLO_5x5}"
TIER="${TIER:-DIAMOND}"
DIVISION="${DIVISION:-I}"
PAGE="${PAGE:-1}"
MATCH_START="${MATCH_START:-0}"
MATCH_COUNT="${MATCH_COUNT:-20}"
ACTIVE_SHARD_GAME="${ACTIVE_SHARD_GAME:-lol}"

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
    "account_by_puuid" \
    "GET" \
    "${BASE_URL}/riot/account/v1/accounts/by-puuid/${SAMPLE_PUUID}?regional_route=${ACCOUNT_REGIONAL_ROUTE}" \
    "200"

  run_http \
    "account_active_shard" \
    "GET" \
    "${BASE_URL}/riot/account/v1/active-shards/by-game/${ACTIVE_SHARD_GAME}/by-puuid/${SAMPLE_PUUID}?regional_route=${ACCOUNT_REGIONAL_ROUTE}" \
    "200,404"

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

  run_http \
    "summoner_by_puuid" \
    "GET" \
    "${BASE_URL}/lol/summoner/v4/summoners/by-puuid/${SAMPLE_PUUID}?platform_route=${PLATFORM_ROUTE}" \
    "200"
else
  record_skip "Set SAMPLE_PUUID to exercise Account-V1, Match-V5, League-V4, and Summoner-V4 PUUID endpoints."
fi

if [[ -n "${SAMPLE_RIOT_GAME_NAME:-}" && -n "${SAMPLE_RIOT_TAG_LINE:-}" ]]; then
  riot_game_name_segment="$(url_path_segment "${SAMPLE_RIOT_GAME_NAME}")"
  riot_tag_line_segment="$(url_path_segment "${SAMPLE_RIOT_TAG_LINE}")"
  riot_id_query="$(url_path_segment "${SAMPLE_RIOT_GAME_NAME}#${SAMPLE_RIOT_TAG_LINE}")"

  run_http \
    "account_by_riot_id" \
    "GET" \
    "${BASE_URL}/riot/account/v1/accounts/by-riot-id/${riot_game_name_segment}/${riot_tag_line_segment}?regional_route=${ACCOUNT_REGIONAL_ROUTE}" \
    "200"

  run_http \
    "profile_fetch_by_riot_id" \
    "POST" \
    "${BASE_URL}/profiles/fetch?riot_id=${riot_id_query}&account_regional_route=${ACCOUNT_REGIONAL_ROUTE}&platform_route=${PLATFORM_ROUTE}&regional_route=${REGIONAL_ROUTE}" \
    "202"
else
  record_skip "Set SAMPLE_RIOT_GAME_NAME and SAMPLE_RIOT_TAG_LINE to exercise Account-V1 Riot ID lookup and profile fetching."
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
