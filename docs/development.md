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

Trigger the first ingestion flow:

```bash
curl "http://localhost:8000/ingestion/ladder-page?platform_route=oc1&queue=RANKED_SOLO_5x5&tier=CHALLENGER"
```

Or send the same inputs with the HTTP `QUERY` method:

```bash
curl -X QUERY "http://localhost:8000/ingestion/ladder-page" \
  -H "Content-Type: application/json" \
  -d '{"platform_route":"oc1","queue":"RANKED_SOLO_5x5","tier":"DIAMOND","division":"I","page":1}'
```

The flow is:

```text
Ladder endpoint -> players -> PUUIDs
```

Challenger, Grandmaster, and Master use Riot's apex League-V4 endpoints, which do not take a division or page. Lower tiers use the entries endpoint with `division` and optional `page`.

This stage does not persist data to PostgreSQL and does not fetch Match-V5 history or match details.

## Branch and PR Expectations

Keep changes focused and easy to review. Include tests for new behavior, update documentation when commands or architecture change, and make sure formatting, linting, typing, and tests pass before opening a pull request.

## Secrets

Do not commit `.env` or real Riot API keys. Use `.env.example` for documented defaults and keep local secrets in `.env`.

## Future Implementation Stages

Planned stages include Match-V5 history discovery, database models and Alembic migrations, persisted deduplication by `match_id`, and background worker orchestration.
