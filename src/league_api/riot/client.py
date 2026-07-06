import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import httpx

from league_api.core.config import Settings, get_settings
from league_api.riot.errors import (
    RiotApiError,
    RiotConfigurationError,
    RiotRateLimitError,
    RiotRateLimitWouldWaitError,
)
from league_api.riot.rate_limiter import (
    RiotRateLimitAudience,
    RiotRateLimitManager,
    get_riot_rate_limiter,
)
from league_api.riot.routing import (
    DEFAULT_ACCOUNT_REGIONAL_ROUTE,
    DEFAULT_OCE_PLATFORM_ROUTE,
    DEFAULT_OCE_REGIONAL_ROUTE,
    RiotAccountRegionalRoute,
    RiotPlatformRoute,
    RiotRegionalRoute,
    get_account_regional_base_url,
    get_platform_base_url,
    get_regional_base_url,
)

logger = logging.getLogger(__name__)

RiotRequestEventHandler = Callable[["RiotRequestEvent"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RiotRequestEvent:
    event_type: str
    method: str
    url: str
    path: str
    occurred_at: datetime
    attempt: int
    status_code: int | None = None
    wait_seconds: float | None = None
    resume_at: datetime | None = None
    retry_after: str | None = None
    rate_limit_reason: str | None = None
    error: str | None = None


@dataclass(slots=True)
class RiotClient:
    """Async Riot API client for mirrored Riot API routes."""

    api_key: str | None
    platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE
    regional_route: str = DEFAULT_OCE_REGIONAL_ROUTE
    timeout: float = 10.0
    rate_limiter: RiotRateLimitManager | None = None
    request_event_handler: RiotRequestEventHandler | None = None
    request_logs_enabled: bool = False
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
    ) -> "RiotClient":
        resolved_settings = settings or get_settings()
        return cls(
            api_key=resolved_settings.riot_api_key,
            platform_route=resolved_settings.default_platform_route,
            regional_route=resolved_settings.default_regional_route,
            rate_limiter=get_riot_rate_limiter(resolved_settings),
            request_event_handler=request_event_handler,
            request_logs_enabled=resolved_settings.riot_request_logs_enabled,
        )

    async def __aenter__(self) -> "RiotClient":
        self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str | RiotRegionalRoute = DEFAULT_OCE_REGIONAL_ROUTE,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        return await self._get_json(
            get_regional_base_url(regional_route),
            path,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
        )

    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str | RiotAccountRegionalRoute = DEFAULT_ACCOUNT_REGIONAL_ROUTE,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        return await self._get_json(
            get_account_regional_base_url(regional_route),
            path,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
        )

    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str | RiotPlatformRoute = DEFAULT_OCE_PLATFORM_ROUTE,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        return await self._get_json(
            get_platform_base_url(platform_route),
            path,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
        )

    async def get_summoner_v4(
        self,
        path: str,
        *,
        platform_route: str | RiotPlatformRoute = DEFAULT_OCE_PLATFORM_ROUTE,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        return await self._get_json(
            get_platform_base_url(platform_route),
            path,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
        )

    async def _get_json(
        self,
        base_url: str,
        path: str,
        *,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        if not self.api_key:
            msg = "RIOT_API_KEY is required before calling the Riot API."
            raise RiotConfigurationError(msg)

        client = self._ensure_client()
        filtered_params = (
            {key: value for key, value in params.items() if value is not None}
            if params is not None
            else None
        )
        attempts = 0
        url = f"{base_url}{path}"
        while True:
            current_attempt = attempts + 1

            async def emit_budget_wait(
                delay: float,
                *,
                attempt: int = current_attempt,
            ) -> None:
                await self._emit_rate_limit_wait(
                    url=url,
                    path=path,
                    attempt=attempt,
                    wait_seconds=delay,
                    reason="riot_rate_limit",
                )

            if self.rate_limiter is not None:
                if wait_for_rate_limit:
                    await self.rate_limiter.acquire(
                        audience=rate_limit_audience,
                        on_wait=emit_budget_wait,
                    )
                else:
                    acquired, wait_seconds = self.rate_limiter.try_acquire(
                        audience=rate_limit_audience
                    )
                    if not acquired:
                        msg = "Riot request would wait for rate-limit capacity."
                        raise RiotRateLimitWouldWaitError(msg, wait_seconds=wait_seconds)

            await self._emit_event(
                RiotRequestEvent(
                    event_type="request_started",
                    method="GET",
                    url=url,
                    path=path,
                    occurred_at=datetime.now(UTC),
                    attempt=current_attempt,
                )
            )

            try:
                response = await client.get(url, params=filtered_params)
            except httpx.HTTPError as exc:
                msg = f"Riot request failed before receiving a response: {exc.__class__.__name__}"
                await self._emit_event(
                    RiotRequestEvent(
                        event_type="request_failed",
                        method="GET",
                        url=url,
                        path=path,
                        occurred_at=datetime.now(UTC),
                        attempt=current_attempt,
                        error=msg,
                    )
                )
                raise RiotApiError(msg) from exc

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                await self._emit_event(
                    RiotRequestEvent(
                        event_type="request_failed",
                        method="GET",
                        url=url,
                        path=path,
                        occurred_at=datetime.now(UTC),
                        attempt=current_attempt,
                        status_code=response.status_code,
                        retry_after=retry_after,
                        error="Riot API rate limit exceeded.",
                    )
                )
                if self.rate_limiter is None or attempts >= self.rate_limiter.max_retries:
                    retry_text = f" Retry-After: {retry_after}." if retry_after else ""
                    msg = f"Riot API rate limit exceeded.{retry_text}"
                    raise RiotRateLimitError(msg, retry_after=retry_after)

                attempts += 1
                retry_attempt = attempts + 1

                async def emit_retry_wait(
                    delay: float,
                    *,
                    attempt: int = retry_attempt,
                    retry_after_value: str | None = retry_after,
                ) -> None:
                    await self._emit_rate_limit_wait(
                        url=url,
                        path=path,
                        attempt=attempt,
                        wait_seconds=delay,
                        reason="riot_429",
                        retry_after=retry_after_value,
                    )

                await self.rate_limiter.pause_for_retry_after(
                    retry_after,
                    on_wait=emit_retry_wait,
                )
                continue

            if response.status_code < 200 or response.status_code >= 300:
                msg = f"Riot API request failed with status {response.status_code}."
                await self._emit_event(
                    RiotRequestEvent(
                        event_type="request_failed",
                        method="GET",
                        url=url,
                        path=path,
                        occurred_at=datetime.now(UTC),
                        attempt=current_attempt,
                        status_code=response.status_code,
                        error=msg,
                    )
                )
                raise RiotApiError(msg, status_code=response.status_code)

            await self._emit_event(
                RiotRequestEvent(
                    event_type="request_succeeded",
                    method="GET",
                    url=url,
                    path=path,
                    occurred_at=datetime.now(UTC),
                    attempt=current_attempt,
                    status_code=response.status_code,
                )
            )
            return response.json()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"X-Riot-Token": self.api_key or ""},
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def _emit_rate_limit_wait(
        self,
        *,
        url: str,
        path: str,
        attempt: int,
        wait_seconds: float,
        reason: str,
        retry_after: str | None = None,
    ) -> None:
        resume_at = datetime.now(UTC) + timedelta(seconds=wait_seconds)
        await self._emit_event(
            RiotRequestEvent(
                event_type="rate_limit_wait",
                method="GET",
                url=url,
                path=path,
                occurred_at=datetime.now(UTC),
                attempt=attempt,
                wait_seconds=wait_seconds,
                resume_at=resume_at,
                retry_after=retry_after,
                rate_limit_reason=reason,
            )
        )

    async def _emit_event(self, event: RiotRequestEvent) -> None:
        self._log_event(event)

        if self.request_event_handler is not None:
            await self.request_event_handler(event)

    def _log_event(self, event: RiotRequestEvent) -> None:
        if not self.request_logs_enabled:
            return

        limit_label = self.rate_limiter.limit_label if self.rate_limiter is not None else "none"
        if event.event_type == "rate_limit_wait":
            logger.info(
                "Riot      rate-limit wait limit=%s reason=%s resumes_at=%s wait=%.1fs "
                'attempt=%s path="%s"',
                limit_label,
                event.rate_limit_reason,
                event.resume_at.isoformat() if event.resume_at is not None else None,
                event.wait_seconds or 0,
                event.attempt,
                event.path,
            )
        elif event.event_type == "request_failed":
            if event.status_code is None:
                logger.warning(
                    'Riot      "GET %s" failed attempt=%s limit=%s error="%s"',
                    event.path,
                    event.attempt,
                    limit_label,
                    event.error,
                )
            else:
                logger.warning(
                    'Riot      "GET %s" %s %s attempt=%s retry_after=%s limit=%s',
                    event.path,
                    event.status_code,
                    _status_phrase(event.status_code),
                    event.attempt,
                    event.retry_after,
                    limit_label,
                )
        elif event.event_type == "request_succeeded":
            logger.info(
                'Riot      "GET %s" %s %s attempt=%s limit=%s',
                event.path,
                event.status_code,
                _status_phrase(event.status_code),
                event.attempt,
                limit_label,
            )
        else:
            logger.info(
                'Riot      "GET %s" started attempt=%s limit=%s',
                event.path,
                event.attempt,
                limit_label,
            )


def _status_phrase(status_code: int | None) -> str:
    if status_code is None:
        return ""
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Unknown"
