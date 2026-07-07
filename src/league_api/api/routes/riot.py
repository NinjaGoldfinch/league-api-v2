from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, HTTPException, Request
from starlette.responses import JSONResponse

from league_api.riot.client import RiotClient, get_last_riot_cache_headers
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError


def get_riot_client(request: Request) -> RiotClient:
    return RiotClient.from_settings(
        cache_store=getattr(request.app.state, "riot_cache_store", None),
        rate_limiter=getattr(request.app.state, "riot_rate_limiter", None),
    )


RiotClientDependency = Depends(get_riot_client)


async def call_riot(operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        payload = await operation()
        cache_headers = get_last_riot_cache_headers()
        if not cache_headers:
            return payload
        return JSONResponse(
            content=payload,
            headers=cache_headers,
        )
    except RiotConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.message,
            headers=get_last_riot_cache_headers() or None,
        ) from exc
    except RiotRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=exc.message,
            headers=get_last_riot_cache_headers() or None,
        ) from exc
    except RiotApiError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=exc.message,
            headers=get_last_riot_cache_headers() or None,
        ) from exc
