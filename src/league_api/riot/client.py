from dataclasses import dataclass, field
from typing import Any

import httpx

from league_api.core.config import Settings, get_settings
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError
from league_api.riot.routing import (
    DEFAULT_OCE_PLATFORM_ROUTE,
    DEFAULT_OCE_REGIONAL_ROUTE,
    RiotPlatformRoute,
    RiotRegionalRoute,
    get_platform_base_url,
    get_regional_base_url,
)


@dataclass(slots=True)
class RiotClient:
    """Async Riot API client for Match-V5 and League-V4 mirror routes."""

    api_key: str | None
    platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE
    regional_route: str = DEFAULT_OCE_REGIONAL_ROUTE
    timeout: float = 10.0
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RiotClient":
        resolved_settings = settings or get_settings()
        return cls(
            api_key=resolved_settings.riot_api_key,
            platform_route=resolved_settings.default_platform_route,
            regional_route=resolved_settings.default_regional_route,
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
    ) -> Any:
        return await self._get_json(
            get_regional_base_url(regional_route),
            path,
            params=params,
        )

    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str | RiotPlatformRoute = DEFAULT_OCE_PLATFORM_ROUTE,
        params: dict[str, int | str | None] | None = None,
    ) -> Any:
        return await self._get_json(
            get_platform_base_url(platform_route),
            path,
            params=params,
        )

    async def _get_json(
        self,
        base_url: str,
        path: str,
        *,
        params: dict[str, int | str | None] | None = None,
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
        try:
            response = await client.get(f"{base_url}{path}", params=filtered_params)
        except httpx.HTTPError as exc:
            msg = f"Riot request failed before receiving a response: {exc.__class__.__name__}"
            raise RiotApiError(msg) from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            retry_text = f" Retry-After: {retry_after}." if retry_after else ""
            msg = f"Riot API rate limit exceeded.{retry_text}"
            raise RiotRateLimitError(msg, retry_after=retry_after)

        if response.status_code < 200 or response.status_code >= 300:
            msg = f"Riot API request failed with status {response.status_code}."
            raise RiotApiError(msg, status_code=response.status_code)

        return response.json()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"X-Riot-Token": self.api_key or ""},
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client
