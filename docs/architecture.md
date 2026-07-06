# Architecture

This project is structured as a small FastAPI backend that mirrors selected Riot
API surfaces with minimal local logic.

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

## Scope

There is no local ingestion, persistence, deduplication, or background worker
logic in the current stage. The app only authenticates with Riot, forwards GET
requests for the mirrored endpoints, and returns Riot JSON payloads directly.
