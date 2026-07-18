from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from league_api.core import auth
from league_api.core.config import Settings


@pytest.fixture(autouse=True)
def reset_mutation_windows() -> Iterator[None]:
    auth._mutation_windows.clear()
    yield
    auth._mutation_windows.clear()


@pytest.mark.asyncio
async def test_accepts_valid_bearer_when_custom_token_header_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(operator_api_token="secret"))

    await auth.require_operator_token(
        _request(),
        authorization="Bearer secret",
        x_operator_token="stale-secret",
    )


@pytest.mark.asyncio
async def test_rejects_request_when_neither_supplied_token_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(operator_api_token="secret"))

    with pytest.raises(HTTPException) as caught:
        await auth.require_operator_token(
            _request(),
            authorization="Bearer wrong-bearer",
            x_operator_token="wrong-custom-token",
        )

    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_auth_rate_limit_cannot_be_bypassed_with_arbitrary_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(operator_api_token=None, operator_mutation_requests=1)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    await auth.require_operator_token(_request(), x_operator_token="first-arbitrary-value")

    with pytest.raises(HTTPException) as caught:
        await auth.require_operator_token(_request(), x_operator_token="second-arbitrary-value")

    assert caught.value.status_code == 429


@pytest.mark.asyncio
async def test_retry_after_rounds_up_to_cover_the_remaining_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        operator_api_token="secret",
        operator_mutation_requests=1,
        operator_mutation_window_seconds=1.5,
    )
    timestamps = iter([100.0, 100.1])
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr("league_api.core.auth._monotonic", lambda: next(timestamps))

    await auth.require_operator_token(_request(), authorization="Bearer secret")

    with pytest.raises(HTTPException) as caught:
        await auth.require_operator_token(_request(), authorization="Bearer secret")

    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "2"}


def _settings(
    *,
    operator_api_token: str | None,
    operator_mutation_requests: int = 30,
    operator_mutation_window_seconds: float = 60.0,
) -> Settings:
    return Settings(
        OPERATOR_API_TOKEN=operator_api_token,
        OPERATOR_MUTATION_REQUESTS=operator_mutation_requests,
        OPERATOR_MUTATION_WINDOW_SECONDS=operator_mutation_window_seconds,
    )


def _request() -> Request:
    return Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
