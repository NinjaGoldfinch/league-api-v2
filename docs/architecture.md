# Architecture

This project is structured as a small FastAPI backend that can grow into Riot API ingestion without mixing concerns too early.

## API Layer

The `league_api.api` package owns HTTP routes. It currently exposes only `GET /health`, with future public and operational endpoints expected to live under route modules.

## Riot Client Layer

The `league_api.riot` package will own Riot API routing and HTTP client behavior. Platform routes are used for League-V4 ladder endpoints, while regional routes are used for Match-V5 endpoints. For OCE, ladder data uses the `oc1` platform route and Match-V5 uses the `sea` regional route.

## Ingestion Services

The `league_api.ingestion` package is reserved for orchestration code that will collect ladder pages, discover player match IDs, and fetch match details. The first planned ingestion path is OCE Challenger ranked ladder data.

## Database and Repository Layer

The `league_api.db` package contains async SQLAlchemy session setup. Future database models belong in `league_api.models`, and repository-style data access can be added when persistence behavior becomes concrete.

## Background Worker Layer

Future ingestion work may need background workers for scheduled ladder refreshes, match history discovery, and match detail fetching. That layer should call ingestion services rather than embedding Riot or database logic directly in worker entry points.

## Match Deduplication

Ladder ingestion should deduplicate matches by `match_id`. Multiple Challenger players can appear in the same game, so match history discovery will naturally find repeated match IDs. Deduplicating before fetching and storing Match-V5 details avoids redundant Riot calls, reduces rate-limit pressure, and keeps database writes idempotent.
