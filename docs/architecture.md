# Architecture

This project is structured as a small FastAPI backend that mirrors selected Riot
API surfaces with minimal local logic.

## API Layer

The `league_api.api` package owns HTTP routes. It currently exposes GET-only
mirrors for Riot Account-V1, Match-V5, League-V4, and Summoner-V4:

```text
/riot/account/v1/accounts/by-puuid/{puuid}
/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
/riot/account/v1/active-shards/by-game/{game}/by-puuid/{puuid}
/lol/match/v5/matches/by-puuid/{puuid}/ids
/lol/match/v5/matches/by-puuid/{puuid}/replays
/lol/match/v5/matches/{matchId}
/lol/match/v5/matches/{matchId}/timeline
/lol/league/v4/challengerleagues/by-queue/{queue}
/lol/league/v4/entries/by-puuid/{encryptedPUUID}
/lol/league/v4/entries/{queue}/{tier}/{division}
/lol/league/v4/grandmasterleagues/by-queue/{queue}
/lol/league/v4/masterleagues/by-queue/{queue}
/lol/summoner/v4/summoners/by-puuid/{encryptedPUUID}
POST /profiles/fetch
GET/QUERY /profiles/fetch
GET/QUERY /jobs/status
```

Account-V1 and Match-V5 routes accept `regional_route` to choose the Riot
regional host. League-V4 and Summoner-V4 routes accept `platform_route` to
choose the Riot platform host. Those routing parameters are local proxy
configuration; Riot's documented path and query parameters are otherwise passed
through as-is.

`POST /profiles/fetch` is a process-local profile workflow. It accepts a
`gameName#tagLine` Riot ID, tries to resolve Account-V1, Summoner-V4, and recent
Match-V5 IDs without waiting on manual rate-limit capacity, then queues the
remaining profile work through the job system. Profile jobs have higher queue
priority than automatic ladder ingestion.

`GET /profiles/fetch` and `QUERY /profiles/fetch` read the same cached profile
view without starting work. `GET /jobs/status` and `QUERY /jobs/status` expose
the same job list view. The QUERY aliases follow RFC 10008 for safe structured
reads with JSON request bodies and advertise `Accept-Query: "application/json"`.
Missing QUERY `Content-Type` returns `400`, unsupported media returns `415`, and
invalid JSON or schema failures return `422`.

The API layer also exposes generic job routes:

```text
POST /jobs/ingestion/ladder
GET/QUERY /jobs/status
GET /jobs/{job_id}
GET /jobs/{job_id}/result
```

`POST /jobs/ingestion/ladder` is intentionally parameterised instead of split
into separate Challenger, Grandmaster, Master, or ranked-page start endpoints.
Only `ladder=challenger` is implemented now; later stages can add
`grandmaster`, `master`, and `ranked_page` through the same endpoint.

## Riot Client Layer

The `league_api.riot` package owns Riot API routing, error types, and HTTP
client behavior. Platform routes are used for League-V4 and Summoner-V4.
Regional routes are used for Account-V1 and Match-V5. The client returns Riot
JSON payloads directly so response shape stays aligned with Riot's own DTOs.
When caching is enabled, mirrored GET responses are cached by normalized method,
host, path, and query parameters. Cache status is exposed through
`X-League-API-Cache` rather than by changing Riot payloads.

## Job Layer

The `league_api.jobs` package owns background work:

```text
models.py
store.py
postgres_store.py
queue.py
ingestion.py
```

`store.py` defines the job-store boundary and keeps the in-memory test
implementation. `postgres_store.py` persists queued, running, succeeded, and
failed job records, progress, events, errors, and results. The queue still uses
one `asyncio.PriorityQueue` worker inside the FastAPI process, with Redis job
locks preventing duplicate processing when multiple API processes are running.
`league_api.main` creates `app.state.job_store`, `app.state.job_queue`,
`app.state.riot_cache_store`, and `app.state.riot_rate_limiter` during lifespan
startup and stops the worker during shutdown.

The current ladder ingestion job fetches the OCE Challenger
`RANKED_SOLO_5x5` ladder from League-V4, treats ladder entry PUUIDs as the source
of players, fetches 20 recent Match-V5 match IDs per PUUID, deduplicates match
IDs, and fetches each unique match detail once. It does not call Account-V1 or
Summoner-V4.

## Persistence and Local Services

PostgreSQL is the durable source for generic Riot response cache entries and job
state. Redis is used for shared job locks and Riot rate-limit coordination. The
Docker Compose stack includes the API, PostgreSQL, Redis, an Alembic migration
service, Adminer, and RedisInsight.

External workers, normalized analytics tables, pgBouncer, PostgREST or Hasura,
and observability services are intentionally deferred until workload and query
patterns are clearer.
