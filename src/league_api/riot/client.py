import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from inspect import isawaitable
from typing import Any, cast

import httpx

from league_api.core.config import Settings, get_settings
from league_api.riot.cache import RiotCacheStore, build_riot_cache_key, ttl_for_riot_path
from league_api.riot.errors import (
    RiotApiError,
    RiotConfigurationError,
    RiotRateLimitError,
    RiotRateLimitWouldWaitError,
)
from league_api.riot.rate_limiter import (
    RiotRateLimitAudience,
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
_last_cache_status: ContextVar[str | None] = ContextVar("last_riot_cache_status", default=None)
_last_cache_headers: ContextVar[dict[str, str] | None] = ContextVar(
    "last_riot_cache_headers",
    default=None,
)


def get_last_riot_cache_status() -> str | None:
    return _last_cache_status.get()


def get_last_riot_cache_headers() -> dict[str, str]:
    return _last_cache_headers.get() or {}


def _error_label(exc: Exception) -> str:
    return exc.__class__.__name__


def _safe_header_value(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")[:200]


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
    rate_limiter: Any | None = None
    cache_store: RiotCacheStore | None = None
    cache_enabled: bool = False
    cache_stale_while_revalidate_seconds: int = 0
    settings: Settings | None = None
    request_event_handler: RiotRequestEventHandler | None = None
    request_logs_enabled: bool = False
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
        cache_store: RiotCacheStore | None = None,
        rate_limiter: Any | None = None,
    ) -> "RiotClient":
        resolved_settings = settings or get_settings()
        return cls(
            api_key=resolved_settings.riot_api_key,
            platform_route=resolved_settings.default_platform_route,
            regional_route=resolved_settings.default_regional_route,
            rate_limiter=rate_limiter or get_riot_rate_limiter(resolved_settings),
            cache_store=cache_store,
            cache_enabled=resolved_settings.cache_enabled and cache_store is not None,
            cache_stale_while_revalidate_seconds=(
                resolved_settings.cache_stale_while_revalidate_seconds
            ),
            settings=resolved_settings,
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
        bypass_cache: bool = False,
    ) -> Any:
        return await self._get_json(
            get_regional_base_url(regional_route),
            path,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
            bypass_cache=bypass_cache,
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
        bypass_cache: bool = False,
    ) -> Any:
        client = self._ensure_client()
        _last_cache_status.set(None)
        _last_cache_headers.set({})
        filtered_params = (
            {key: value for key, value in params.items() if value is not None}
            if params is not None
            else None
        )
        attempts = 0
        url = f"{base_url}{path}"
        cache_key = build_riot_cache_key(
            method="GET",
            base_url=base_url,
            path=path,
            params=cast(dict[str, int | str | None] | None, filtered_params),
        )
        if bypass_cache:
            self._set_cache_headers("bypass", read="bypass")
        elif not self.cache_enabled:
            if self.settings is not None and self.settings.cache_enabled:
                self._set_cache_headers("bypass", read="unavailable", write="unavailable")
            else:
                self._set_cache_headers("bypass", read="disabled", write="disabled")
        elif self.cache_store is None:
            self._set_cache_headers("bypass", read="unavailable", write="unavailable")
        else:
            try:
                cached_entry = await self.cache_store.get(cache_key.cache_key)
            except Exception as exc:
                self._set_cache_headers("error", read="error", error=f"read:{_error_label(exc)}")
                logger.warning(
                    "Riot cache read failed; continuing with live Riot request. "
                    'path="%s" cache_key=%s error_type=%s',
                    path,
                    cache_key.cache_key,
                    exc.__class__.__name__,
                    exc_info=True,
                )
            else:
                cache_status = cached_entry.status_at() if cached_entry is not None else None
                if cached_entry is not None and cache_status is not None:
                    self._set_cache_headers(cache_status, read=cache_status)
                    logger.info(
                        'Riot cache %s path="%s" cache_key=%s',
                        cache_status,
                        path,
                        cache_key.cache_key,
                    )
                    return cached_entry.payload
                self._set_cache_headers("miss", read="miss")

        if not self.api_key:
            msg = "RIOT_API_KEY is required before calling the Riot API."
            raise RiotConfigurationError(msg)

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
                    acquire_result = self.rate_limiter.try_acquire(audience=rate_limit_audience)
                    if isawaitable(acquire_result):
                        acquire_result = await acquire_result
                    acquired, wait_seconds = cast(tuple[bool, float], acquire_result)
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
            payload = response.json()
            if self.cache_enabled and self.cache_store is not None and self.settings is not None:
                ttl_seconds = ttl_for_riot_path(path, self.settings)
                if ttl_seconds > 0:
                    try:
                        await self.cache_store.put(
                            key=cache_key,
                            payload=payload,
                            status_code=response.status_code,
                            headers={key: value for key, value in response.headers.items()},
                            ttl_seconds=ttl_seconds,
                            stale_while_revalidate_seconds=self.cache_stale_while_revalidate_seconds,
                        )
                    except Exception as exc:
                        current_headers = get_last_riot_cache_headers()
                        current_error = current_headers.get("X-League-API-Cache-Error")
                        error = f"write:{_error_label(exc)}"
                        if current_error:
                            error = f"{current_error};{error}"
                        self._merge_cache_headers(
                            status="error",
                            write="error",
                            error=error,
                        )
                        logger.warning(
                            "Riot cache write failed; returning live Riot response. "
                            'path="%s" cache_key=%s error_type=%s',
                            path,
                            cache_key.cache_key,
                            exc.__class__.__name__,
                            exc_info=True,
                        )
                    else:
                        self._merge_cache_headers(write="stored")
                        logger.info(
                            'Riot cache stored path="%s" cache_key=%s ttl_seconds=%s',
                            path,
                            cache_key.cache_key,
                            ttl_seconds,
                        )
                elif get_last_riot_cache_status() is None:
                    self._set_cache_headers("bypass", write="disabled")
            return payload

    def _set_cache_headers(
        self,
        status: str,
        *,
        read: str | None = None,
        write: str | None = None,
        error: str | None = None,
    ) -> None:
        _last_cache_status.set(status)
        headers = {"X-League-API-Cache": status}
        if read is not None:
            headers["X-League-API-Cache-Read"] = read
        if write is not None:
            headers["X-League-API-Cache-Write"] = write
        if error is not None:
            headers["X-League-API-Cache-Error"] = _safe_header_value(error)
        _last_cache_headers.set(headers)

    def _merge_cache_headers(
        self,
        *,
        status: str | None = None,
        read: str | None = None,
        write: str | None = None,
        error: str | None = None,
    ) -> None:
        headers = dict(get_last_riot_cache_headers())
        resolved_status = (
            status or headers.get("X-League-API-Cache") or get_last_riot_cache_status() or "miss"
        )
        headers["X-League-API-Cache"] = resolved_status
        if read is not None:
            headers["X-League-API-Cache-Read"] = read
        if write is not None:
            headers["X-League-API-Cache-Write"] = write
        if error is not None:
            headers["X-League-API-Cache-Error"] = _safe_header_value(error)
        _last_cache_status.set(resolved_status)
        _last_cache_headers.set(headers)

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
