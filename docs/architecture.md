# Architecture

This project is structured as a small FastAPI backend that can grow into Riot API ingestion without mixing concerns too early.

## API Layer

The `league_api.api` package owns HTTP routes. It exposes `GET /health` and `GET /ingestion/ladder-page` for the first Riot ingestion path. `QUERY /ingestion/ladder-page` is also supported for clients that need to send the ladder request inputs as JSON body content.

## Riot Client Layer

The `league_api.riot` package owns Riot API routing, minimal response schemas, error types, and HTTP client behavior. Platform routes are used for League-V4 ladder endpoints. For OCE, ladder data uses the `oc1` platform route.

## Ingestion Services

The `league_api.ingestion` package owns orchestration code that collects ladder entries. The first ingestion path is:

```text
Ladder endpoint -> players -> PUUIDs
```

This stage deliberately does not use Account-V1, Summoner-V4, or Match-V5. League-V4 ladder entries provide PUUIDs directly. Challenger, Grandmaster, and Master use Riot's apex ladder endpoints, while lower tiers use the division/page entries endpoint.

## Database and Repository Layer

The `league_api.db` package contains async SQLAlchemy session setup. Future database models belong in `league_api.models`, and repository-style data access can be added when persistence behavior becomes concrete.

## Background Worker Layer

Future ingestion work may need background workers for scheduled ladder refreshes, match history discovery, and match detail fetching. That layer should call ingestion services rather than embedding Riot or database logic directly in worker entry points.

## Match Deduplication

A later Match-V5 stage should deduplicate matches by `match_id`. Multiple ranked players can appear in the same game, so match history discovery will naturally find repeated match IDs. That later stage should check persisted match IDs before fetching details and should store fetched match payloads outside the API response.
