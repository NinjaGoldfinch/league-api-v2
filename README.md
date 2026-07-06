# League API

League API is a Python 3.12+ FastAPI backend for League of Legends game data.
The first working ingestion path pulls an OCE ranked ladder page and returns the League-V4 entries with a small summary.

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

## Run

```bash
uvicorn league_api.main:app --reload
```

The health endpoint is available at `GET /health`.

## First Ingestion Flow

```text
Ladder endpoint -> players -> PUUIDs
```

Run an OCE Challenger Solo Queue ladder fetch:

```bash
curl "http://localhost:8000/ingestion/ladder-page?platform_route=oc1&queue=RANKED_SOLO_5x5&tier=CHALLENGER"
```

The same route also accepts the HTTP `QUERY` method when a client needs to send
query inputs in a JSON body instead of the URL:

```bash
curl -X QUERY "http://localhost:8000/ingestion/ladder-page" \
  -H "Content-Type: application/json" \
  -d '{"platform_route":"oc1","queue":"RANKED_SOLO_5x5","tier":"DIAMOND","division":"I","page":1}'
```

Challenger, Grandmaster, and Master use Riot's apex League-V4 endpoints, which do not take a division or page. Lower tiers use the entries endpoint with `division` and optional `page`.

This stage does not persist data to PostgreSQL yet and does not fetch Match-V5 history or match details.

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
