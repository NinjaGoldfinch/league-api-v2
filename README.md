# League API

League API is a Python 3.12+ FastAPI backend foundation for League of Legends game data.
Future stages will add Riot API ingestion for ladder pages, player match histories, and Match-V5 match details, beginning with OCE Challenger ranked ladder data.

Riot ingestion is not implemented yet. This repository currently contains the project structure, configuration, health endpoint, database session scaffolding, and development tooling.

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

Copy the environment example:

```bash
cp .env.example .env
```

The Riot API key is optional during this foundation stage.

## Run

```bash
uvicorn league_api.main:app --reload
```

The health endpoint is available at `GET /health`.

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
