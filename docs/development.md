# Development

## Formatting

Use Ruff for formatting:

```bash
ruff format .
```

Check formatting without changing files:

```bash
ruff format --check .
```

## Linting

Use Ruff for linting:

```bash
ruff check .
```

## Typing

Use mypy:

```bash
mypy
```

## Testing

Use pytest:

```bash
pytest
```

## Combined Checks

Run the same local checks expected by CI:

```bash
make check
```

## Local App Run

Add a Riot development key to `.env`:

```env
RIOT_API_KEY=your-development-key
```

Start FastAPI locally:

```bash
make local
```

`make local` installs development dependencies, starts PostgreSQL and Redis with
Docker Compose when Docker is available, runs Alembic migrations, and starts
Uvicorn. Use `make compose` for the full container stack with Adminer at
`http://localhost:8080` and RedisInsight at `http://localhost:5540`.

Fetch an Account-V1 account:

```bash
curl "http://localhost:8000/riot/account/v1/accounts/by-puuid/PLAYER_PUUID?regional_route=asia"
```

Fetch Match-V5 match IDs:

```bash
curl "http://localhost:8000/lol/match/v5/matches/by-puuid/PLAYER_PUUID/ids?regional_route=sea&start=0&count=20"
```

Fetch a League-V4 Challenger page:

```bash
curl "http://localhost:8000/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?platform_route=oc1"
```

Fetch a League-V4 entries page:

```bash
curl "http://localhost:8000/lol/league/v4/entries/RANKED_SOLO_5x5/DIAMOND/I?platform_route=oc1&page=1"
```

Fetch a Summoner-V4 summoner:

```bash
curl "http://localhost:8000/lol/summoner/v4/summoners/by-puuid/PLAYER_PUUID?platform_route=oc1"
```

Queue a profile fetch by Riot ID:

```bash
curl -X POST "http://localhost:8000/profiles/fetch?riot_id=GAME_NAME%23TAG_LINE&account_regional_route=asia&platform_route=oc1&regional_route=sea"
```

Repeated fetch requests for the same Riot ID and route reuse an existing queued
or running job.

Read a cached profile with RFC 10008 `QUERY` when you want a structured JSON
body instead of URL query parameters:

```bash
curl -X QUERY "http://localhost:8000/profiles/fetch" \
  -H "Content-Type: application/json" \
  -d '{"riot_id":"GAME_NAME#TAG_LINE","account_regional_route":"asia","platform_route":"oc1","regional_route":"sea"}'
```

Read the frontend-friendly composed profile view without starting work:

```bash
curl "http://localhost:8000/profiles/by-riot-id/GAME_NAME/TAG_LINE?account_regional_route=asia&platform_route=oc1&regional_route=sea&match_start=0&match_limit=15"
curl -X QUERY "http://localhost:8000/profiles/by-riot-id" \
  -H "Content-Type: application/json" \
  -d '{"riot_id":"GAME_NAME#TAG_LINE","account_regional_route":"asia","platform_route":"oc1","regional_route":"sea","match_start":0,"match_limit":15}'
```

The composed view returns at most 15 compact match summaries by default and
caps `match_limit` at 50. Use the response `matches_pagination.next_start` as
the next `match_start` to load every fetched match without returning the whole
history in one response.

Profile lifecycle information is grouped by purpose: `status` is the concise
reader-facing lifecycle (`missing`, `populating`, `ready`, `refreshing`, or
`failed`), `data_summary` describes which resources and match details are
available, and `progress` contains live counters and estimates for active work.
`diagnostics` keeps cache states and full active/latest job records—including
waits, errors, retained events, and request estimates—out of the primary status
summary. `status.operation` is `initial_population` for a profile without a
previous successful job and `refresh` when existing successful data is being
updated.

Only `GET` is supported for mirrored Riot endpoints. Use `/docs` to inspect the
full current local OpenAPI documentation.

Start an in-memory ladder ingestion job:

```bash
curl -X POST "http://localhost:8000/jobs/ingestion/ladder?platform_route=oc1&regional_route=sea&queue=RANKED_SOLO_5x5&ladder=challenger&match_count=20"
```

Poll status and result:

```bash
curl "http://localhost:8000/jobs/JOB_ID"
curl "http://localhost:8000/jobs/JOB_ID/result"
```

List active jobs or expand to all retained job details:

```bash
curl "http://localhost:8000/jobs/status"
curl "http://localhost:8000/jobs/status?running_only=false&verbose=true&include_events=true&include_result=true"
curl "http://localhost:8000/jobs/status?status=queued&status=running&job_type=profile_fetch&riot_id=GAME_NAME%23TAG_LINE&limit=25"
curl -X QUERY "http://localhost:8000/jobs/status" \
  -H "Content-Type: application/json" \
  -d '{"running_only":false,"status":["succeeded"],"job_type":"profile_fetch","riot_id":"GAME_NAME#TAG_LINE","limit":25}'
```

Job responses include a `details` object with the Riot source, queue, tier,
division, route, match count, player count, and request-count context. They also
include an `estimate` object with the current fetching stage, completed and
remaining Riot request counts, and `estimated_completed_at` when there is enough
progress to project a rough finish time. The estimate uses the slower of
observed request pace and the configured Riot app rate limit, plus any active
rate-limit wait.
List responses include `limit`, `has_more`, and `next_cursor`; pass
`next_cursor` back as `cursor` to request the next page.

When the Riot client is waiting on rate limits, job responses include
`current_wait.resume_at` plus recent `events` entries for request start,
success, failure, and rate-limit waits.
Set `RIOT_REQUEST_LOGS_ENABLED=false` to turn off the matching console logs.

`QUERY` requests must send `Content-Type: application/json`. Successful QUERY
capable responses advertise `Accept-Query: "application/json"`. For a separate
browser frontend, configure `CORS_ALLOWED_ORIGINS` as a JSON array, for example
`["http://localhost:5173"]`; QUERY is not CORS-safelisted and will use an
OPTIONS preflight.

Run all live endpoint smoke scripts:

```bash
make test-endpoints
```

Include the live endpoint scripts at the end of `make check`:

```bash
RUN_LIVE_ENDPOINTS=1 make check
```

The overall runner calls separate scripts for Riot mirror endpoints and job
endpoints. It prints request logs, status codes, success/failure summaries, and
paths to full response bodies and headers. Endpoints that require sample data
are skipped unless `SAMPLE_PUUID`, `SAMPLE_MATCH_ID`, or
`SAMPLE_RIOT_GAME_NAME` plus `SAMPLE_RIOT_TAG_LINE` are set.

Run one group directly when narrowing a failure:

```bash
make test-riot-endpoints
make test-job-endpoints
```

Useful flags:

```bash
BASE_URL="http://localhost:8000" make test-endpoints
SAMPLE_PUUID="PLAYER_PUUID" SAMPLE_MATCH_ID="OC1_123" make test-endpoints
SAMPLE_RIOT_GAME_NAME="GAME_NAME" SAMPLE_RIOT_TAG_LINE="TAG_LINE" make test-endpoints
JOB_WAIT_FOR_COMPLETION=1 JOB_TIMEOUT_SECONDS=300 make test-endpoints
SHOW_RESPONSE_BODY=1 make test-endpoints
```

When `DATABASE_URL` is configured, job state, progress, events, errors, and
results are stored in PostgreSQL. When `REDIS_URL` is configured, the API uses
Redis for job locks and shared Riot rate-limit coordination. Without those
settings, tests and lightweight local runs can still use in-memory fallbacks.
Riot mirror endpoints use the generic response cache when `CACHE_ENABLED=true`
and expose `X-League-API-Cache: miss|hit|stale` without changing Riot JSON
payloads.

Account-V1 and Summoner-V4 are available as mirrored base endpoints but are not
needed for ladder ingestion because League-V4 ladder entries are treated as the
source of PUUIDs.
Profile fetch jobs use higher queue priority than ladder ingestion, and manual
profile Account-V1, Summoner-V4, and match-ID calls reserve 20% of the Riot app
rate-limit budget by default. Automatic work can use that reserved budget in the
last 10 seconds before a rate-limit window resets.

## Branch and PR Expectations

Keep changes focused and easy to review. Include tests for new behavior, update documentation when commands or architecture change, and make sure formatting, linting, typing, and tests pass before opening a pull request.

## Secrets

Do not commit `.env` or real Riot API keys. Use `.env.example` for documented defaults and keep local secrets in `.env`.

## Current Scope

Keep ingestion in this stage limited to the generic `/jobs/ingestion/ladder`
start endpoint, `/jobs/status`, and the `/jobs/{job_id}` status/result
endpoints. The current job supports OCE Challenger only: it fetches the ladder,
requests 20 recent Match-V5 match IDs per PUUID, deduplicates IDs, and fetches
each unique match detail once. Grandmaster, Master, ranked-page ingestion,
normalized analytics tables and external workers are future stages.
