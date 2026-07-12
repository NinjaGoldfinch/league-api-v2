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
from league_api.matches.store import InMemoryMatchStore, MatchStore
from league_api.riot.client import RiotClient, RiotRequestEventHandler
from league_api.riot.rate_limiter import RiotRateLimitAudience
from league_api.riot.routing import RiotAccountRegionalRoute, RiotPlatformRoute, RiotRegionalRoute

MATCH_ID_PAGE_SIZE = 100


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
        bypass_cache: bool = False,
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
    match_store: MatchStore | None = None,
) -> ProfileFetchResult:
    resolved_match_store = match_store or InMemoryMatchStore()
    progress = JobProgress()
    errors: list[JobError] = []
    matches: dict[str, dict[str, Any]] = {}
    unique_match_ids: list[str] = []
    seen_match_ids: set[str] = set()

    async with riot_client_factory(
        request_event_handler=_job_riot_request_event_handler(store, job_id)
    ) as riot_client:
        account = params.account
        if account is None:
            account = await _fetch_account(riot_client, params)
        progress.players_discovered = 1
        await store.update_progress(job_id, progress)

        puuid = _puuid_from_account(account)
        history_match_ids = await resolved_match_store.get_player_match_ids(puuid)
        known_match_ids = set(history_match_ids)
        matches.update(await resolved_match_store.get_matches(history_match_ids))
        summoner = params.summoner
        if summoner is None:
            summoner = await _fetch_summoner(riot_client, params, puuid)
        progress.players_processed = 1
        await store.update_progress(job_id, progress)

        if params.match_ids is not None:
            await _record_and_process_match_id_page(
                riot_client=riot_client,
                store=store,
                job_id=job_id,
                params=params,
                progress=progress,
                account=account,
                summoner=summoner,
                page_match_ids=params.match_ids,
                unique_match_ids=unique_match_ids,
                seen_match_ids=seen_match_ids,
                matches=matches,
                errors=errors,
                match_store=resolved_match_store,
                puuid=puuid,
                known_match_ids=known_match_ids,
                history_match_ids=history_match_ids,
            )
        else:
            start = 0
            remaining = params.match_count
            while remaining is None or remaining > 0:
                count = (
                    MATCH_ID_PAGE_SIZE if remaining is None else min(MATCH_ID_PAGE_SIZE, remaining)
                )
                page_match_ids = await _fetch_match_id_page(
                    riot_client,
                    params,
                    puuid,
                    start=start,
                    count=count,
                )
                progress.match_id_pages_fetched += 1
                if page_match_ids:
                    progress.match_id_pages_with_results += 1
                reached_known_match = await _record_and_process_match_id_page(
                    riot_client=riot_client,
                    store=store,
                    job_id=job_id,
                    params=params,
                    progress=progress,
                    account=account,
                    summoner=summoner,
                    page_match_ids=page_match_ids,
                    unique_match_ids=unique_match_ids,
                    seen_match_ids=seen_match_ids,
                    matches=matches,
                    errors=errors,
                    match_store=resolved_match_store,
                    puuid=puuid,
                    known_match_ids=known_match_ids,
                    history_match_ids=history_match_ids,
                )
                if reached_known_match or len(page_match_ids) < count:
                    break
                start += len(page_match_ids)
                if remaining is not None:
                    remaining -= len(page_match_ids)

    return ProfileFetchResult(
        summary=progress,
        account=account,
        summoner=summoner,
        match_ids=_merge_match_ids(unique_match_ids, history_match_ids),
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


async def _fetch_match_id_page(
    riot_client: ProfileRiotApiClient,
    params: ProfileFetchParams,
    puuid: str,
    *,
    start: int,
    count: int,
    wait_for_rate_limit: bool = True,
) -> list[str]:
    match_ids_payload = await riot_client.get_match_v5(
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
        regional_route=params.regional_route,
        params={"start": start, "count": count},
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=wait_for_rate_limit,
        bypass_cache=True,
    )
    if not isinstance(match_ids_payload, list):
        msg = "Match ID response did not return a list."
        raise ValueError(msg)
    return [match_id for match_id in match_ids_payload if isinstance(match_id, str) and match_id]


async def _record_and_process_match_id_page(
    *,
    riot_client: ProfileRiotApiClient,
    store: JobStore,
    job_id: str,
    params: ProfileFetchParams,
    progress: JobProgress,
    account: dict[str, Any],
    summoner: dict[str, Any],
    page_match_ids: list[str],
    unique_match_ids: list[str],
    seen_match_ids: set[str],
    matches: dict[str, dict[str, Any]],
    errors: list[JobError],
    match_store: MatchStore,
    puuid: str,
    known_match_ids: set[str],
    history_match_ids: list[str],
) -> bool:
    page_unique_match_ids: list[str] = []
    reached_known_match = False
    progress.match_ids_discovered += len(page_match_ids)
    for match_id in page_match_ids:
        if match_id in seen_match_ids:
            progress.duplicate_match_ids_skipped += 1
            continue
        if match_id in known_match_ids:
            reached_known_match = True
            continue
        seen_match_ids.add(match_id)
        unique_match_ids.append(match_id)
        page_unique_match_ids.append(match_id)
    progress.unique_match_ids = len(unique_match_ids)
    await store.update_progress(job_id, progress)
    await _update_partial_result(
        store=store,
        job_id=job_id,
        progress=progress,
        account=account,
        summoner=summoner,
        match_ids=_merge_match_ids(unique_match_ids, history_match_ids),
        matches=matches,
        errors=errors,
    )

    stored_matches = await match_store.get_matches(page_unique_match_ids)
    for match_id in page_unique_match_ids:
        match_payload = stored_matches.get(match_id)
        if match_payload is None:
            fetched_payload = await riot_client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}",
                regional_route=params.regional_route,
                rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
            )
            if not isinstance(fetched_payload, dict):
                msg = f"Match detail for {match_id} did not return an object."
                raise ValueError(msg)
            match_payload = cast(dict[str, Any], fetched_payload)
            await match_store.save_match(
                match_id,
                regional_route=str(params.regional_route),
                payload=match_payload,
            )
        await match_store.link_player_matches(puuid, [match_id])
        matches[match_id] = match_payload
        progress.matches_fetched += 1
        await store.update_progress(job_id, progress)
        await _update_partial_result(
            store=store,
            job_id=job_id,
            progress=progress,
            account=account,
            summoner=summoner,
            match_ids=_merge_match_ids(unique_match_ids, history_match_ids),
            matches=matches,
            errors=errors,
        )
    return reached_known_match


def _merge_match_ids(new_match_ids: list[str], history_match_ids: list[str]) -> list[str]:
    return list(dict.fromkeys([*new_match_ids, *history_match_ids]))


async def _update_partial_result(
    *,
    store: JobStore,
    job_id: str,
    progress: JobProgress,
    account: dict[str, Any],
    summoner: dict[str, Any],
    match_ids: list[str],
    matches: dict[str, dict[str, Any]],
    errors: list[JobError],
) -> None:
    await store.update_result(
        job_id,
        result=ProfileFetchResult(
            summary=progress.model_copy(deep=True),
            account=account,
            summoner=summoner,
            match_ids=match_ids,
            matches=matches.copy(),
            errors=[error.model_copy(deep=True) for error in errors],
        ),
    )


def _puuid_from_account(account: dict[str, Any]) -> str:
    puuid = account.get("puuid")
    if not isinstance(puuid, str) or not puuid:
        msg = "Account-V1 response did not include a PUUID."
        raise ValueError(msg)
    return puuid


def _path_segment(value: str) -> str:
    return quote(value, safe="")
