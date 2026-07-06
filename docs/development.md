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
uvicorn league_api.main:app --reload
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
```

Job responses include a `details` object with the Riot source, queue, tier,
division, route, match count, player count, and request-count context. They also
include an `estimate` object with the current fetching stage, completed and
remaining Riot request counts, and `estimated_completed_at` when there is enough
progress to project a rough finish time. The estimate uses the slower of
observed request pace and the configured Riot app rate limit, plus any active
rate-limit wait.

When the Riot client is waiting on rate limits, job responses include
`current_wait.resume_at` plus recent `events` entries for request start,
success, failure, and rate-limit waits.
Set `RIOT_REQUEST_LOGS_ENABLED=false` to turn off the matching console logs.

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
are skipped unless `SAMPLE_PUUID` or `SAMPLE_MATCH_ID` are set.

Run one group directly when narrowing a failure:

```bash
make test-riot-endpoints
make test-job-endpoints
```

Useful flags:

```bash
BASE_URL="http://localhost:8000" make test-endpoints
SAMPLE_PUUID="PLAYER_PUUID" SAMPLE_MATCH_ID="OC1_123" make test-endpoints
JOB_WAIT_FOR_COMPLETION=1 JOB_TIMEOUT_SECONDS=300 make test-endpoints
SHOW_RESPONSE_BODY=1 make test-endpoints
```

The job system is in-memory and process-local. Restarting FastAPI clears queued,
running, completed, and failed jobs. This stage intentionally avoids Redis,
databases, persistent caches, and external worker frameworks. Account-V1 is not
needed because League-V4 ladder entries are treated as the source of PUUIDs.

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
persistence, retries, rate-limit scheduling, and external workers are future
stages.
