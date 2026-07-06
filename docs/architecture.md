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

The API layer also exposes generic job routes:

```text
POST /jobs/ingestion/ladder
GET /jobs/{job_id}
GET /jobs/{job_id}/result
```

`POST /jobs/ingestion/ladder` is intentionally parameterised instead of split
into separate Challenger, Grandmaster, Master, or ranked-page start endpoints.
Only `ladder=challenger` is implemented now; later stages can add
`grandmaster`, `master`, and `ranked_page` through the same endpoint.

## Riot Client Layer

The `league_api.riot` package owns Riot API routing, error types, and HTTP
client behavior. Platform routes are used for League-V4. Regional routes are
used for Match-V5. The client returns Riot JSON payloads directly so response
shape stays aligned with Riot's own DTOs.

## Job Layer

The `league_api.jobs` package owns process-local background work:

```text
models.py
store.py
queue.py
ingestion.py
```

The store keeps `queued`, `running`, `succeeded`, and `failed` job records in
memory behind an `asyncio.Lock`. The queue uses one `asyncio.Queue` worker that
processes jobs sequentially inside the FastAPI process. `league_api.main`
creates `app.state.job_store` and `app.state.job_queue` during lifespan startup
and stops the worker during shutdown.

The current ladder ingestion job fetches the OCE Challenger
`RANKED_SOLO_5x5` ladder from League-V4, treats ladder entry PUUIDs as the source
of players, fetches 20 recent Match-V5 match IDs per PUUID, deduplicates match
IDs, and fetches each unique match detail once. It does not call Account-V1.

## Scope

Job state and results are not persistent. They are lost on process restart, and
there is no Redis, database, persistent cache, Celery, RQ, Dramatiq, or ARQ in
this stage. Production-grade persistence, retries, rate-limit scheduling, and
external workers are future stages.
