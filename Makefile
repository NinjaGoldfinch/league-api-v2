.PHONY: install run test lint format typecheck check

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

run:
	uvicorn league_api.main:app --reload

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy

check:
	ruff format --check .
	ruff check .
	mypy
	pytest
