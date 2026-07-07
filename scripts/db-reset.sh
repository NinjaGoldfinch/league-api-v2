#!/usr/bin/env bash

set -euo pipefail

if [[ "${CONFIRM_DESTROY:-0}" != "1" ]]; then
  printf '[ERROR] This deletes local Postgres and Redis compose volumes.\n' >&2
  printf '[ERROR] Re-run with CONFIRM_DESTROY=1 to continue.\n' >&2
  exit 1
fi

docker compose down --volumes --remove-orphans
docker compose up --build
