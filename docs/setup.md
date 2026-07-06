# Setup

## Python Version

Use Python 3.12 or newer.

## Virtual Environment

Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

## Install Dependencies

Install the package with development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Or use the Makefile:

```bash
make install
```

## Environment Variables

Copy the example file:

```bash
cp .env.example .env
```

`RIOT_API_KEY` is required for mirrored Riot Match-V5 and League-V4 calls. It
may be left empty when running local tests that override the Riot client.

## Run the API Locally

```bash
uvicorn league_api.main:app --reload
```

Then check:

```bash
curl http://127.0.0.1:8000/openapi.json
```

## Run Tests

```bash
pytest
```
