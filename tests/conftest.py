from collections.abc import Generator

import pytest

from league_api.core.config import get_settings


@pytest.fixture(autouse=True)
def use_test_environment(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
