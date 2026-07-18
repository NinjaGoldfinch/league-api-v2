import asyncio
import math
import time
from collections import defaultdict, deque
from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from league_api.core.config import get_settings

_mutation_windows: dict[str, deque[float]] = defaultdict(deque)
_mutation_lock = asyncio.Lock()
_monotonic = time.monotonic


async def require_operator_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_operator_token: Annotated[str | None, Header()] = None,
) -> None:
    """Protect operator mutations when an operator token is configured."""
    expected = get_settings().operator_api_token
    bearer = None
    if authorization is not None:
        scheme, separator, credentials = authorization.partition(" ")
        if separator and scheme.casefold() == "bearer":
            bearer = credentials
    supplied_tokens = [token for token in (x_operator_token, bearer) if token is not None]
    if expected and not any(compare_digest(token, expected) for token in supplied_tokens):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid operator token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    client_key = (
        "operator-token"
        if expected
        else (request.client.host if request.client is not None else "unknown")
    )
    settings = get_settings()
    now = _monotonic()
    cutoff = now - settings.operator_mutation_window_seconds
    async with _mutation_lock:
        window = _mutation_windows[client_key]
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= settings.operator_mutation_requests:
            retry_after = max(
                1,
                math.ceil(window[0] + settings.operator_mutation_window_seconds - now),
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Operator mutation rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)
