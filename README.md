# League API

League API is a Python 3.12+ FastAPI backend that currently mirrors Riot
Match-V5 and League-V4 GET endpoints. It keeps the local URL paths aligned with
Riot's documented paths and adds a small routing query parameter for choosing
the Riot upstream region or platform.

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
