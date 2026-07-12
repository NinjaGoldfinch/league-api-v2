#!/usr/bin/env bash

set -euo pipefail

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://league_api:league_api@localhost:5432/league_api}"

printf '[INFO] Running Alembic migrations against %s\n' "${DATABASE_URL}"
alembic upgrade head
