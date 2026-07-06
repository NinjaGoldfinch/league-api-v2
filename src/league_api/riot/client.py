from dataclasses import dataclass, field
from typing import Any

import httpx

from league_api.core.config import Settings, get_settings
from league_api.riot.errors import RiotApiError, RiotConfigurationError, RiotRateLimitError
from league_api.riot.routing import (
    DEFAULT_OCE_PLATFORM_ROUTE,
    DEFAULT_OCE_REGIONAL_ROUTE,
    get_platform_base_url,
    get_regional_base_url,
)
from league_api.riot.schemas import LeagueEntry

APEX_TIER_PATHS = {
    "CHALLENGER": "challengerleagues",
    "GRANDMASTER": "grandmasterleagues",
    "MASTER": "masterleagues",
}


@dataclass(slots=True)
class RiotClient:
    """Async Riot API client for the first ingestion stage."""

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

    async def fetch_ladder_page(
        self,
        queue: str,
        tier: str,
        division: str | None = None,
        page: int | None = None,
        platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE,
    ) -> list[LeagueEntry]:
        apex_path = APEX_TIER_PATHS.get(tier.upper())
        if apex_path is not None:
            data = await self._get_json(
                get_platform_base_url(platform_route),
                f"/lol/league/v4/{apex_path}/by-queue/{queue}",
            )
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                msg = "Riot apex ladder response did not contain an entries list."
                raise RiotApiError(msg)
            return [LeagueEntry.model_validate(entry) for entry in data["entries"]]

        if not division:
            msg = "division is required for non-apex ranked ladder tiers."
            raise RiotApiError(msg)

        data = await self._get_json(
            get_platform_base_url(platform_route),
            f"/lol/league/v4/entries/{queue}/{tier}/{division}",
            params={"page": page or 1},
        )
        if not isinstance(data, list):
            msg = "Riot ladder response was not a list."
            raise RiotApiError(msg)
        return [LeagueEntry.model_validate(entry) for entry in data]

    async def fetch_match_ids_by_puuid(
        self,
        puuid: str,
        start: int = 0,
        count: int = 20,
        regional_route: str = DEFAULT_OCE_REGIONAL_ROUTE,
    ) -> list[str]:
        data = await self._get_json(
            get_regional_base_url(regional_route),
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params={"start": start, "count": count},
        )
        if not isinstance(data, list) or not all(isinstance(match_id, str) for match_id in data):
            msg = "Riot match history response was not a list of match IDs."
            raise RiotApiError(msg)
        return data

    async def fetch_match_by_id(
        self,
        match_id: str,
        regional_route: str = DEFAULT_OCE_REGIONAL_ROUTE,
    ) -> dict[str, Any]:
        data = await self._get_json(
            get_regional_base_url(regional_route),
            f"/lol/match/v5/matches/{match_id}",
        )
        if not isinstance(data, dict):
            msg = "Riot match detail response was not an object."
            raise RiotApiError(msg)
        return data

    async def _get_json(
        self,
        base_url: str,
        path: str,
        *,
        params: dict[str, int] | None = None,
    ) -> Any:
        if not self.api_key:
            msg = "RIOT_API_KEY is required before calling the Riot API."
            raise RiotConfigurationError(msg)

        client = self._ensure_client()
        try:
            response = await client.get(f"{base_url}{path}", params=params)
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
