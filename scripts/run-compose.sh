#!/usr/bin/env bash

set -euo pipefail

if [[ ! -f ".env" ]]; then
  printf '[INFO] Creating .env from .env.example\n'
  cp .env.example .env
fi

docker compose up --build
