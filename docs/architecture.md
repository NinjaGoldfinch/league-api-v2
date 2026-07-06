# Architecture

This project is structured as a small FastAPI backend that mirrors selected Riot
API surfaces without mixing proxy behavior, persistence, and ingestion concerns
too early.

## API Layer

The `league_api.api` package owns HTTP routes. It currently exposes GET-only
mirrors for Riot Match-V5 and League-V4:

```text
/lol/match/v5/matches/by-puuid/{puuid}/ids
/lol/match/v5/matches/by-puuid/{puuid}/replays
/lol/match/v5/matches/{matchId}
/lol/match/v5/matches/{matchId}/timeline
/lol/league/v4/challengerleagues/by-queue/{queue}
/lol/league/v4/entries/by-puuid/{encryptedPUUID}
/lol/league/v4/entries/{queue}/{tier}/{division}
/lol/league/v4/grandmasterleagues/by-queue/{queue}
/lol/league/v4/masterleagues/by-queue/{queue}
```

Match-V5 routes accept `regional_route` to choose the Riot regional host.
League-V4 routes accept `platform_route` to choose the Riot platform host.
Those routing parameters are local proxy configuration; Riot's documented path
and query parameters are otherwise passed through as-is.

## Riot Client Layer

The `league_api.riot` package owns Riot API routing, error types, and HTTP
client behavior. Platform routes are used for League-V4. Regional routes are
used for Match-V5. The client returns Riot JSON payloads directly so response
shape stays aligned with Riot's own DTOs.

## Ingestion Services

Ingestion orchestration is intentionally not exposed through the API right now.
Future ingestion services should build on the Riot client instead of embedding
Riot HTTP behavior directly in route handlers.

## Database and Repository Layer

The `league_api.db` package contains async SQLAlchemy session setup. Future database models belong in `league_api.models`, and repository-style data access can be added when persistence behavior becomes concrete.

## Background Worker Layer

There is no public background worker API in the current mirror stage. Future
scheduled ladder refreshes, match history discovery, and match detail fetching
should reuse the Riot client and keep queue concerns outside the HTTP mirror
routes.

## Match Deduplication

The mirror routes do not deduplicate or persist data. Later persisted ingestion
stages should deduplicate by `match_id`, check stored match IDs before fetching
details, and store fetched match payloads outside the API response.
