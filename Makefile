.PHONY: install run test lint format typecheck check test-endpoints test-riot-endpoints test-job-endpoints

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
	@if [ "$${RUN_LIVE_ENDPOINTS:-0}" = "1" ]; then \
		$(MAKE) test-endpoints; \
	else \
		echo "Skipping live endpoint scripts. Run RUN_LIVE_ENDPOINTS=1 make check to include them."; \
	fi

test-endpoints:
	bash scripts/test-endpoints.sh

test-riot-endpoints:
	bash scripts/test-riot-endpoints.sh

test-job-endpoints:
	bash scripts/test-job-endpoints.sh
