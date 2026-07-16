import asyncio
import time
from collections import defaultdict, deque
from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from league_api.core.config import get_settings

_mutation_windows: dict[str, deque[float]] = defaultdict(deque)
_mutation_lock = asyncio.Lock()


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
    supplied = x_operator_token or bearer
    if expected and (supplied is None or not compare_digest(supplied, expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid operator token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    client_key = supplied or (request.client.host if request.client is not None else "unknown")
    settings = get_settings()
    now = time.monotonic()
    cutoff = now - settings.operator_mutation_window_seconds
    async with _mutation_lock:
        window = _mutation_windows[client_key]
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= settings.operator_mutation_requests:
            retry_after = max(1, int(window[0] + settings.operator_mutation_window_seconds - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Operator mutation rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)
