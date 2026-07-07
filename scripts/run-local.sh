#!/usr/bin/env bash

set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [[ ! -f ".env" ]]; then
  printf '[INFO] Creating .env from .env.example\n'
  cp .env.example .env
fi

python -m pip install -e ".[dev]"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://league_api:league_api@localhost:5432/league_api}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export CACHE_ENABLED="${CACHE_ENABLED:-true}"
START_LOCAL_SERVICES="${START_LOCAL_SERVICES:-1}"

if [[ "${START_LOCAL_SERVICES}" == "1" ]] && command -v docker >/dev/null 2>&1; then
  printf '[INFO] Starting local Postgres and Redis with Docker Compose\n'
  docker compose up -d postgres redis
fi

bash scripts/db-migrate.sh

printf '[INFO] Starting League API at http://%s:%s\n' "${HOST}" "${PORT}"
uvicorn league_api.main:app --host "${HOST}" --port "${PORT}" --reload
