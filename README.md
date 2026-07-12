# League API

League API is a Python 3.12+ FastAPI backend that currently mirrors Riot
Account-V1, Match-V5, League-V4, and Summoner-V4 GET endpoints. It keeps the
local URL paths aligned with Riot's documented paths and adds a small routing
query parameter for choosing the Riot upstream region or platform.

The app also includes a background job system for early ingestion work. It can
run fully in memory for tests, or use PostgreSQL for durable job state and Riot
response caching plus Redis for shared job locks and Riot rate-limit
coordination.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

## Setup

Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Install the project with development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Copy the environment example and add a local Riot development key:

```bash
cp .env.example .env
```

```env
RIOT_API_KEY=your-development-key
```

Riot calls use a process-local app rate limiter by default. The default budget
matches the development-key app limit: `20` requests per `1` second and `100`
requests per `120` seconds. Tune these values with
`RIOT_APP_RATE_LIMIT_SHORT_REQUESTS`, `RIOT_APP_RATE_LIMIT_SHORT_WINDOW_SECONDS`,
`RIOT_APP_RATE_LIMIT_LONG_REQUESTS`, and `RIOT_APP_RATE_LIMIT_LONG_WINDOW_SECONDS`.
If Riot still returns `429`, the client waits for `Retry-After` and retries the
same request up to `RIOT_RATE_LIMIT_MAX_RETRIES`.
Manual profile requests reserve 20% of the configured app budget by default.
Automatic requests can use that reserved capacity during the last 10 seconds
before a rate-limit window resets.
Set `RIOT_REQUEST_LOGS_ENABLED=false` to suppress Riot request console logs.
When enabled, Riot logs use compact request lines such as
`Riot      "GET /lol/match/v5/matches/OC1_1" 200 OK attempt=1 limit=20/1s-100/120s`
and rate-limit waits include the same limit label plus `resumes_at`.

## Run

Start the full development stack with PostgreSQL, Redis, Adminer, RedisInsight,
database migrations, and the API:

```bash
make compose
```

For local Python development with Docker-managed PostgreSQL and Redis:

```bash
make local
```

Adminer is available at `http://localhost:8080`, RedisInsight at
`http://localhost:5540`, and the API at `http://localhost:8000`.

To run only the API process yourself:

```bash
uvicorn league_api.main:app --reload
```

OpenAPI documentation is available at `GET /docs` and `GET /openapi.json`.
Mirrored Riot responses expose cache metadata with `X-League-API-Cache` set to
`miss`, `hit`, or `stale` when caching is enabled.
First-party read endpoints can also advertise RFC 10008 `QUERY` support with
`Accept-Query: "application/json"` when they accept structured JSON query
bodies.

## Account-V1

Account-V1 endpoints use regional routing. Set `regional_route` to `AMERICAS`,
`ASIA`, or `EUROPE`; it defaults to `asia`.

Fetch an account by PUUID or Riot ID:

```bash
curl "http://localhost:8000/riot/account/v1/accounts/by-puuid/PLAYER_PUUID?regional_route=asia"
curl "http://localhost:8000/riot/account/v1/accounts/by-riot-id/GAME_NAME/TAG_LINE?regional_route=asia"
```

Fetch the active shard for a player:

```bash
curl "http://localhost:8000/riot/account/v1/active-shards/by-game/lol/by-puuid/PLAYER_PUUID?regional_route=asia"
```

## Match-V5

Match-V5 endpoints use regional routing. Set `regional_route` to `AMERICAS`,
`ASIA`, `EUROPE`, or `SEA`; it defaults to `sea`.

Fetch match IDs for a player:

```bash
curl "http://localhost:8000/lol/match/v5/matches/by-puuid/PLAYER_PUUID/ids?regional_route=sea&start=0&count=20"
```

The match ID endpoint supports Riot's full query flag set:
`startTime`, `endTime`, `queue`, `type`, `start`, and `count`.

```bash
curl "http://localhost:8000/lol/match/v5/matches/by-puuid/PLAYER_PUUID/ids?regional_route=sea&startTime=1710000000&endTime=1710003600&queue=420&type=ranked&start=0&count=100"
```

Fetch match detail, timeline, or replays:

```bash
curl "http://localhost:8000/lol/match/v5/matches/OC1_123456789?regional_route=sea"
curl "http://localhost:8000/lol/match/v5/matches/OC1_123456789/timeline?regional_route=sea"
curl "http://localhost:8000/lol/match/v5/matches/by-puuid/PLAYER_PUUID/replays?regional_route=sea"
```

## League-V4

League-V4 endpoints use platform routing. Set `platform_route` to a Riot
platform such as `OC1`, `NA1`, `EUW1`, `KR`, `SG2`, `TW2`, or `VN2`; it defaults
to `oc1`.

Fetch apex leagues:

```bash
curl "http://localhost:8000/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?platform_route=oc1"
curl "http://localhost:8000/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5?platform_route=oc1"
curl "http://localhost:8000/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5?platform_route=oc1"
```

Fetch entries by PUUID or ranked page:

```bash
curl "http://localhost:8000/lol/league/v4/entries/by-puuid/PLAYER_PUUID?platform_route=oc1"
curl "http://localhost:8000/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I?platform_route=oc1&page=1"
```

## Summoner-V4

Summoner-V4 endpoints use platform routing. Set `platform_route` to a Riot
platform such as `OC1`, `NA1`, `EUW1`, `KR`, `SG2`, `TW2`, or `VN2`; it defaults
to `oc1`.

Fetch a summoner by PUUID:

```bash
curl "http://localhost:8000/lol/summoner/v4/summoners/by-puuid/PLAYER_PUUID?platform_route=oc1"
```

## Profiles

Profile fetches accept a Riot ID search value, resolve Account-V1 and
Summoner-V4 data, then queue paged Match-V5 ID discovery and match detail
fetching behind the existing job status endpoints. Profile work takes priority
over automatic ladder ingestion.

```bash
curl -X POST "http://localhost:8000/profiles/fetch?riot_id=GAME_NAME%23TAG_LINE&account_regional_route=asia&platform_route=oc1&regional_route=sea"
```

The response is always `202 Accepted`. When the initial Account-V1 and
Summoner-V4 calls can run without waiting for manual rate-limit capacity, the
response includes `account` and `summoner`. Match IDs and match summaries are
filled in by the queued job and are visible through the composed profile read
while the job runs.
Repeated profile fetch requests for the same Riot ID and route return the
existing queued or running job instead of creating duplicate work.

Cached profile reads support both the browser-friendly query-string form and an
RFC 10008 `QUERY` form with a JSON body:

```bash
curl "http://localhost:8000/profiles/fetch?riot_id=GAME_NAME%23TAG_LINE&account_regional_route=asia&platform_route=oc1&regional_route=sea"
curl -X QUERY "http://localhost:8000/profiles/fetch" \
  -H "Content-Type: application/json" \
  -d '{"riot_id":"GAME_NAME#TAG_LINE","account_regional_route":"asia","platform_route":"oc1","regional_route":"sea"}'
```

Frontend-facing profile reads are available as a composed read-only view. These
endpoints never call Riot or enqueue work; they return cached data, compact
match summaries from completed profile jobs, and the latest active or terminal
profile job state. Its grouped `status` identifies `missing`, first-time
`populating`, `ready`, later `refreshing`, and terminal `failed` states. Active
work reports `initial_population` or `refresh` as its operation. `data_summary`
separately reports resource availability, match counts, `last_updated_at`, and
`refresh_after`; active counters and estimates live in `progress`, while cache,
event, wait, error, and job details live in `diagnostics`. The bundled web client
automatically populates missing profiles and refreshes ready profiles after
`refresh_after` while continuing to display available data. Ordinary API reads
remain side-effect free. Compact match summaries are paginated with
`match_limit` defaulting to `15` and capped at `50`; use `match_start` to fetch
later pages.

Successful Match-V5 detail payloads are also written to permanent
`riot_matches` storage and linked to players through `player_matches`. Profile
refreshes fetch newest match-ID pages only until they encounter a previously
known match, reuse permanently stored details, and merge newly discovered
matches with the player's existing history. These durable match rows do not
expire. The separate `riot_response_cache` remains disposable: entries are
usable only through their TTL and stale window, and rows past that window are
pruned during subsequent cache writes.

```bash
curl "http://localhost:8000/profiles/by-riot-id/GAME_NAME/TAG_LINE?account_regional_route=asia&platform_route=oc1&regional_route=sea&match_start=0&match_limit=15"
curl -X QUERY "http://localhost:8000/profiles/by-riot-id" \
  -H "Content-Type: application/json" \
  -d '{"riot_id":"GAME_NAME#TAG_LINE","account_regional_route":"asia","platform_route":"oc1","regional_route":"sea","match_start":0,"match_limit":15}'
```

Only `GET` is supported for mirrored Riot routes, because those paths already
map cleanly to Riot resources and simple query flags.

## Ingestion Jobs

Start an in-memory ladder ingestion job:

```bash
curl -X POST "http://localhost:8000/jobs/ingestion/ladder?platform_route=oc1&regional_route=sea&queue=RANKED_SOLO_5x5&ladder=challenger&match_count=20"
```

Poll job status:

```bash
curl "http://localhost:8000/jobs/JOB_ID"
```

List current job status:

```bash
curl "http://localhost:8000/jobs/status"
curl "http://localhost:8000/jobs/status?running_only=false&verbose=true&include_events=true&include_result=true"
curl "http://localhost:8000/jobs/status?status=queued&status=running&job_type=profile_fetch&riot_id=GAME_NAME%23TAG_LINE&limit=25"
curl -X QUERY "http://localhost:8000/jobs/status" \
  -H "Content-Type: application/json" \
  -d '{"running_only":false,"status":["succeeded"],"job_type":"profile_fetch","riot_id":"GAME_NAME#TAG_LINE","limit":25}'
```

Job status responses include a `details` object with the Riot source, platform
route, regional route, queue, queue label, ladder, tier, division, match count
per player, player count, and request-count context. For the current Challenger
apex ladder job, `tier` is `CHALLENGER` and `division` is `null` because the
Riot endpoint is tier-scoped rather than division-scoped.

Job status responses also include an `estimate` object showing the current
fetching stage, a short description of the work in progress, completed/remaining
Riot request counts, percent complete, and a rough `estimated_completed_at` when
the job has enough completed requests to project from. The estimate also exposes
`rate_limit_seconds_remaining` and `rate_limit_label`; the projected finish time
uses the slower of observed request pace and the configured Riot app rate limit,
plus any active rate-limit wait. `GET /jobs/status` returns queued and running
jobs by default; set `running_only=false` to include terminal jobs. Set
`verbose=true` for params, errors, and the latest event, then add
`include_events=true` or `include_result=true` when you need the retained event
history or completed result payloads.
List responses include `limit`, `has_more`, and `next_cursor`; pass the cursor
back as `cursor` to request the next page. Explicit `status` filters override
the default `running_only=true` queued/running filter.

Job status also includes `current_wait` when a Riot rate-limit pause is active.
That object includes `resume_at`, `wait_seconds`, `reason`, and the Riot path
being retried. The `events` list keeps recent Riot request activity, including
request start, success, failure, and rate-limit wait events.

## HTTP Method Model

The API keeps `GET` for simple resource retrieval and browser/shareable URLs,
uses RFC 10008 `QUERY` for safe structured reads with JSON request bodies, and
keeps `POST` for operations that enqueue work or otherwise change local state.
`POST /profiles/fetch` and `POST /jobs/ingestion/ladder` intentionally remain
POST endpoints because they create background jobs.

Browser clients on a separate origin must use CORS preflight for `QUERY`. Set
`CORS_ALLOWED_ORIGINS` to a JSON array such as `["http://localhost:5173"]` to
allow a frontend origin; the app then allows `GET`, `POST`, `QUERY`, and
`OPTIONS`.

A frontend API client should use `read(path, paramsOrBody, { autoRefresh })` for
safe reads, choosing `QUERY` when the criteria are structured or used by polling
dashboards, and `write(path, body)` for `POST` commands. Frontend page URLs can
remain normal `GET` routes and translate to backend `QUERY` during server-side
or loader-style data fetching.

Fetch the final result:

```bash
curl "http://localhost:8000/jobs/JOB_ID/result"
```

The current `ladder=challenger` job fetches the OCE Challenger ladder from
League-V4, extracts PUUIDs directly from the ladder entries, fetches 20 recent
Match-V5 match IDs per PUUID, deduplicates match IDs, and then fetches each
unique match detail once. Account-V1 and Summoner-V4 are mirrored base endpoints
but are not required by this ingestion stage.

This stage intentionally does not use Redis, a database, Celery, RQ, Dramatiq,
ARQ, or a persistent cache. Production-grade persistence and external workers
are future stages. The
`/jobs/ingestion/ladder` endpoint is parameterised so Grandmaster, Master, and
ranked-page ingestion can be added later without creating more start endpoints.

Run the live endpoint smoke scripts against a running local app:

```bash
make test-endpoints
```

Or include them automatically after the normal local checks:

```bash
RUN_LIVE_ENDPOINTS=1 make check
```

The scripts log each request, HTTP status, response summaries, and full response
bodies under a timestamped directory in `/tmp`. Set `SAMPLE_PUUID` and
`SAMPLE_MATCH_ID` to exercise the PUUID, match detail, and timeline endpoints.
Set `SAMPLE_RIOT_GAME_NAME` and `SAMPLE_RIOT_TAG_LINE` to exercise Account-V1
Riot ID lookup and profile fetching. Set `JOB_WAIT_FOR_COMPLETION=1` to poll a
ladder ingestion job until it succeeds or fails.

Run the script groups separately when you only want one surface:

```bash
make test-riot-endpoints
make test-job-endpoints
```

## Test

```bash
pytest
```

## Lint, Format, and Type Check

```bash
ruff format --check .
ruff check .
mypy
```

You can also run all checks with:

```bash
make check
```

## Documentation

- [Setup](docs/setup.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
