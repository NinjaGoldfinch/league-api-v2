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

## Branch and PR Expectations

Keep changes focused and easy to review. Include tests for new behavior, update documentation when commands or architecture change, and make sure formatting, linting, typing, and tests pass before opening a pull request.

## Secrets

Do not commit `.env` or real Riot API keys. Use `.env.example` for documented defaults and keep local secrets in `.env`.

## Current Scope

Keep the project focused on the GET-only Match-V5 and League-V4 mirror routes.
Avoid adding ingestion, persistence, or worker code until those behaviors are
part of an explicit next stage.
