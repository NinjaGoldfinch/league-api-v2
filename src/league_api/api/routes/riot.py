from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException

from league_api.riot.client import RiotClient
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError


def get_riot_client() -> RiotClient:
    return RiotClient.from_settings()


RiotClientDependency = Depends(get_riot_client)


async def call_riot(operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await operation()
    except RiotConfigurationError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except RiotRateLimitError as exc:
        raise HTTPException(status_code=429, detail=exc.message) from exc
    except RiotApiError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=exc.message) from exc
