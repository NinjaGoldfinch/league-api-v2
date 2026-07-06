# League API

League API is a Python 3.12+ FastAPI backend that currently mirrors Riot
Match-V5 and League-V4 GET endpoints. It keeps the local URL paths aligned with
Riot's documented paths and adds a small routing query parameter for choosing
the Riot upstream region or platform.

The app also includes a process-local in-memory job system for early ingestion
work. Job state is kept only inside the running FastAPI process and is lost when
the process restarts.

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
Set `RIOT_REQUEST_LOGS_ENABLED=false` to suppress Riot request console logs.
When enabled, Riot logs use compact request lines such as
`Riot      "GET /lol/match/v5/matches/OC1_1" 200 OK attempt=1 limit=20/1s-100/120s`
and rate-limit waits include the same limit label plus `resumes_at`.

## Run

```bash
uvicorn league_api.main:app --reload
```

OpenAPI documentation is available at `GET /docs` and `GET /openapi.json`.

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

Only `GET` is supported for mirrored Riot routes. There are no request bodies or
custom `QUERY` method aliases.

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

Job status also includes `current_wait` when a Riot rate-limit pause is active.
That object includes `resume_at`, `wait_seconds`, `reason`, and the Riot path
being retried. The `events` list keeps recent Riot request activity, including
request start, success, failure, and rate-limit wait events.

Fetch the final result:

```bash
curl "http://localhost:8000/jobs/JOB_ID/result"
```

The current `ladder=challenger` job fetches the OCE Challenger ladder from
League-V4, extracts PUUIDs directly from the ladder entries, fetches 20 recent
Match-V5 match IDs per PUUID, deduplicates match IDs, and then fetches each
unique match detail once. Account-V1 is not required for this stage.

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
Set `JOB_WAIT_FOR_COMPLETION=1` to poll a ladder ingestion job until it succeeds
or fails.

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
