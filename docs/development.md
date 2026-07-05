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

## Branch and PR Expectations

Keep changes focused and easy to review. Include tests for new behavior, update documentation when commands or architecture change, and make sure formatting, linting, typing, and tests pass before opening a pull request.

## Secrets

Do not commit `.env` or real Riot API keys. Use `.env.example` for documented defaults and keep local secrets in `.env`.

## Future Implementation Stages

Planned stages include Riot API client methods, database models and Alembic migrations, OCE Challenger ladder ingestion, match ID discovery by PUUID, Match-V5 detail ingestion, deduplication by `match_id`, and background worker orchestration.
