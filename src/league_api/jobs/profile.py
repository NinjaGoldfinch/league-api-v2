from types import TracebackType
from typing import Any, Protocol, cast
from urllib.parse import quote

from league_api.jobs.ingestion import _job_riot_request_event_handler
from league_api.jobs.models import (
    JobError,
    JobProgress,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.store import JobStore
from league_api.riot.client import RiotClient, RiotRequestEventHandler
from league_api.riot.rate_limiter import RiotRateLimitAudience
from league_api.riot.routing import RiotAccountRegionalRoute, RiotPlatformRoute, RiotRegionalRoute


class ProfileRiotApiClient(Protocol):
    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str | RiotAccountRegionalRoute = "asia",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...

    async def get_summoner_v4(
        self,
        path: str,
        *,
        platform_route: str | RiotPlatformRoute = "oc1",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str | RiotRegionalRoute = "sea",
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...


class ProfileRiotClientContext(Protocol):
    async def __aenter__(self) -> ProfileRiotApiClient: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class ProfileRiotClientFactory(Protocol):
    def __call__(
        self,
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
    ) -> ProfileRiotClientContext: ...


def _default_profile_riot_client_factory(
    *,
    request_event_handler: RiotRequestEventHandler | None = None,
) -> ProfileRiotClientContext:
    return RiotClient.from_settings(request_event_handler=request_event_handler)


async def run_profile_fetch(
    params: ProfileFetchParams,
    store: JobStore,
    job_id: str,
    *,
    riot_client_factory: ProfileRiotClientFactory = _default_profile_riot_client_factory,
) -> ProfileFetchResult:
    progress = JobProgress()
    errors: list[JobError] = []
    matches: dict[str, dict[str, Any]] = {}

    async with riot_client_factory(
        request_event_handler=_job_riot_request_event_handler(store, job_id)
    ) as riot_client:
        account = params.account
        if account is None:
            account = await _fetch_account(riot_client, params)
        progress.players_discovered = 1
        await store.update_progress(job_id, progress)

        puuid = _puuid_from_account(account)
        summoner = params.summoner
        if summoner is None:
            summoner = await _fetch_summoner(riot_client, params, puuid)
        progress.players_processed = 1
        await store.update_progress(job_id, progress)

        match_ids = params.match_ids
        if match_ids is None:
            match_ids = await _fetch_match_ids(riot_client, params, puuid)
        progress.match_ids_discovered = len(match_ids)
        unique_match_ids = _unique_match_ids(match_ids)
        progress.duplicate_match_ids_skipped = len(match_ids) - len(unique_match_ids)
        progress.unique_match_ids = len(unique_match_ids)
        await store.update_progress(job_id, progress)

        for match_id in unique_match_ids:
            match_payload = await riot_client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}",
                regional_route=params.regional_route,
                rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
            )
            if not isinstance(match_payload, dict):
                msg = f"Match detail for {match_id} did not return an object."
                raise ValueError(msg)
            matches[match_id] = cast(dict[str, Any], match_payload)
            progress.matches_fetched += 1
            await store.update_progress(job_id, progress)

    return ProfileFetchResult(
        summary=progress,
        account=account,
        summoner=summoner,
        match_ids=unique_match_ids,
        matches=matches,
        errors=errors,
    )


async def _fetch_account(
    riot_client: ProfileRiotApiClient,
    params: ProfileFetchParams,
    *,
    wait_for_rate_limit: bool = True,
) -> dict[str, Any]:
    account_payload = await riot_client.get_account_v1(
        "/riot/account/v1/accounts/by-riot-id/"
        f"{_path_segment(params.game_name)}/{_path_segment(params.tag_line)}",
        regional_route=params.account_regional_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=wait_for_rate_limit,
    )
    if not isinstance(account_payload, dict):
        msg = "Account-V1 response did not return an object."
        raise ValueError(msg)
    return cast(dict[str, Any], account_payload)


async def _fetch_summoner(
    riot_client: ProfileRiotApiClient,
    params: ProfileFetchParams,
    puuid: str,
    *,
    wait_for_rate_limit: bool = True,
) -> dict[str, Any]:
    summoner_payload = await riot_client.get_summoner_v4(
        f"/lol/summoner/v4/summoners/by-puuid/{puuid}",
        platform_route=params.platform_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=wait_for_rate_limit,
    )
    if not isinstance(summoner_payload, dict):
        msg = "Summoner-V4 response did not return an object."
        raise ValueError(msg)
    return cast(dict[str, Any], summoner_payload)


async def _fetch_match_ids(
    riot_client: ProfileRiotApiClient,
    params: ProfileFetchParams,
    puuid: str,
    *,
    wait_for_rate_limit: bool = True,
) -> list[str]:
    match_ids_payload = await riot_client.get_match_v5(
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
        regional_route=params.regional_route,
        params={"start": 0, "count": params.match_count},
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=wait_for_rate_limit,
    )
    if not isinstance(match_ids_payload, list):
        msg = "Match ID response did not return a list."
        raise ValueError(msg)
    return [match_id for match_id in match_ids_payload if isinstance(match_id, str) and match_id]


def _puuid_from_account(account: dict[str, Any]) -> str:
    puuid = account.get("puuid")
    if not isinstance(puuid, str) or not puuid:
        msg = "Account-V1 response did not include a PUUID."
        raise ValueError(msg)
    return puuid


def _unique_match_ids(match_ids: list[str]) -> list[str]:
    unique_match_ids: list[str] = []
    seen: set[str] = set()
    for match_id in match_ids:
        if match_id in seen:
            continue
        seen.add(match_id)
        unique_match_ids.append(match_id)
    return unique_match_ids


def _path_segment(value: str) -> str:
    return quote(value, safe="")
