from typing import Annotated, Any, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from league_api.api.query import add_accept_query_header, parse_query_json
from league_api.api.routes.jobs import get_job_queue, get_job_store
from league_api.api.routes.riot import RiotClientDependency
from league_api.jobs.models import JobStatus, JobType, ProfileFetchParams
from league_api.jobs.queue import (
    PROFILE_FETCH_PRIORITY,
    PROFILE_MATCH_DETAILS_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import JobStore
from league_api.riot.cache import RiotCacheEntry, RiotCacheStore, build_riot_cache_key
from league_api.riot.client import RiotClient
from league_api.riot.errors import (
    RiotApiError,
    RiotConfigurationError,
    RiotRateLimitError,
    RiotRateLimitWouldWaitError,
)
from league_api.riot.rate_limiter import RiotRateLimitAudience
from league_api.riot.routing import (
    RiotAccountRegionalRoute,
    RiotPlatformRoute,
    RiotRegionalRoute,
    get_account_regional_base_url,
    get_platform_base_url,
    get_regional_base_url,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: JobType
    status: JobStatus
    identity_status: str
    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None


class ProfileCacheResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_status: str
    account: dict[str, Any]
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    account_cache_status: str
    summoner_cache_status: str | None = None
    match_ids_cache_status: str | None = None


class ProfileCacheQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    riot_id: str = Field(min_length=3, description="Riot ID in gameName#tagLine format.")
    account_regional_route: RiotAccountRegionalRoute = RiotAccountRegionalRoute.ASIA
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA


@router.post(
    "/fetch",
    response_model=ProfileFetchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fetch a profile by Riot ID",
)
async def fetch_profile(
    store: Annotated[JobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    riot_client: Annotated[RiotClient, RiotClientDependency],
    riot_id: Annotated[
        str,
        Query(
            min_length=3,
            description="Riot ID in gameName#tagLine format.",
        ),
    ],
    account_regional_route: Annotated[
        RiotAccountRegionalRoute,
        Query(description="Riot regional route for Account-V1."),
    ] = RiotAccountRegionalRoute.ASIA,
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for Summoner-V4."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5."),
    ] = RiotRegionalRoute.SEA,
) -> ProfileFetchResponse:
    game_name, tag_line = _parse_riot_id(riot_id)
    params = ProfileFetchParams(
        game_name=game_name,
        tag_line=tag_line,
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
    )

    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    identity_status = "queued"

    try:
        async with riot_client:
            account = await _fetch_account_without_wait(riot_client, params)
            summoner = await _fetch_summoner_without_wait(riot_client, params, _puuid(account))
            match_ids = await _fetch_match_ids_without_wait(riot_client, params, _puuid(account))
            identity_status = "resolved"
    except RiotRateLimitWouldWaitError:
        pass
    except RiotConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message
        ) from exc
    except RiotRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=exc.message
        ) from exc
    except RiotApiError as exc:
        raise HTTPException(
            status_code=exc.status_code or status.HTTP_502_BAD_GATEWAY, detail=exc.message
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    queued_params = params.model_copy(
        update={
            "account": account,
            "summoner": summoner,
            "match_ids": match_ids,
        }
    )
    job = await store.create_job(job_type=JobType.PROFILE_FETCH, params=queued_params)
    priority = PROFILE_MATCH_DETAILS_PRIORITY if match_ids is not None else PROFILE_FETCH_PRIORITY
    await job_queue.enqueue(job.job_id, priority=priority)

    return ProfileFetchResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        identity_status=identity_status,
        account=account,
        summoner=summoner,
        match_ids=match_ids,
    )


@router.get(
    "/fetch",
    response_model=ProfileCacheResponse,
    summary="Get cached profile data by Riot ID",
)
async def get_cached_profile(
    request: Request,
    response: Response,
    riot_id: Annotated[
        str,
        Query(
            min_length=3,
            description="Riot ID in gameName#tagLine format.",
        ),
    ],
    account_regional_route: Annotated[
        RiotAccountRegionalRoute,
        Query(description="Riot regional route for Account-V1."),
    ] = RiotAccountRegionalRoute.ASIA,
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for Summoner-V4."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5."),
    ] = RiotRegionalRoute.SEA,
) -> ProfileCacheResponse:
    add_accept_query_header(response)
    return await _get_cached_profile(
        request=request,
        riot_id=riot_id,
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
    )


@router.api_route(
    "/fetch",
    methods=["QUERY"],
    response_model=ProfileCacheResponse,
    summary="Query cached profile data by Riot ID",
)
async def query_cached_profile(
    request: Request,
    response: Response,
) -> ProfileCacheResponse:
    query = await parse_query_json(request, ProfileCacheQueryRequest)
    add_accept_query_header(response)
    return await _get_cached_profile(
        request=request,
        riot_id=query.riot_id,
        account_regional_route=query.account_regional_route,
        platform_route=query.platform_route,
        regional_route=query.regional_route,
    )


async def _get_cached_profile(
    *,
    request: Request,
    riot_id: str,
    account_regional_route: RiotAccountRegionalRoute,
    platform_route: RiotPlatformRoute,
    regional_route: RiotRegionalRoute,
) -> ProfileCacheResponse:
    game_name, tag_line = _parse_riot_id(riot_id)
    params = ProfileFetchParams(
        game_name=game_name,
        tag_line=tag_line,
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
    )
    cache_store = _riot_cache_store(request)

    account_cached = await _get_active_cached_riot_entry(
        cache_store=cache_store,
        base_url=get_account_regional_base_url(params.account_regional_route),
        path=_account_v1_path(params),
        params=None,
    )
    if account_cached is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile is not cached.",
        )

    account_entry, account_cache_status = account_cached
    if not isinstance(account_entry.payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cached Account-V1 profile data is invalid.",
        )
    account = account_entry.payload
    puuid = _puuid(account)

    summoner: dict[str, Any] | None = None
    summoner_cache_status: str | None = None
    summoner_cached = await _get_active_cached_riot_entry(
        cache_store=cache_store,
        base_url=get_platform_base_url(params.platform_route),
        path=_summoner_v4_path(puuid),
        params=None,
    )
    if summoner_cached is not None:
        summoner_entry, summoner_cache_status = summoner_cached
        if not isinstance(summoner_entry.payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cached Summoner-V4 profile data is invalid.",
            )
        summoner = summoner_entry.payload

    match_ids: list[str] | None = None
    match_ids_cache_status: str | None = None
    match_ids_cached = await _get_active_cached_riot_entry(
        cache_store=cache_store,
        base_url=get_regional_base_url(params.regional_route),
        path=_match_ids_v5_path(puuid),
        params={"start": 0, "count": params.match_count},
    )
    if match_ids_cached is not None:
        match_ids_entry, match_ids_cache_status = match_ids_cached
        if not isinstance(match_ids_entry.payload, list):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cached Match-V5 ID profile data is invalid.",
            )
        match_ids = [
            match_id
            for match_id in match_ids_entry.payload
            if isinstance(match_id, str) and match_id
        ]

    return ProfileCacheResponse(
        identity_status="cached",
        account=account,
        summoner=summoner,
        match_ids=match_ids,
        account_cache_status=account_cache_status,
        summoner_cache_status=summoner_cache_status,
        match_ids_cache_status=match_ids_cache_status,
    )


async def _fetch_account_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
) -> dict[str, Any]:
    account_payload = await riot_client.get_account_v1(
        _account_v1_path(params),
        regional_route=params.account_regional_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(account_payload, dict):
        msg = "Account-V1 response did not return an object."
        raise ValueError(msg)
    return account_payload


async def _fetch_summoner_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
    puuid: str,
) -> dict[str, Any]:
    summoner_payload = await riot_client.get_summoner_v4(
        _summoner_v4_path(puuid),
        platform_route=params.platform_route,
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(summoner_payload, dict):
        msg = "Summoner-V4 response did not return an object."
        raise ValueError(msg)
    return summoner_payload


async def _fetch_match_ids_without_wait(
    riot_client: RiotClient,
    params: ProfileFetchParams,
    puuid: str,
) -> list[str]:
    match_ids_payload = await riot_client.get_match_v5(
        _match_ids_v5_path(puuid),
        regional_route=params.regional_route,
        params={"start": 0, "count": params.match_count},
        rate_limit_audience=RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit=False,
    )
    if not isinstance(match_ids_payload, list):
        msg = "Match ID response did not return a list."
        raise ValueError(msg)
    return [match_id for match_id in match_ids_payload if isinstance(match_id, str) and match_id]


def _riot_cache_store(request: Request) -> RiotCacheStore:
    cache_store = getattr(request.app.state, "riot_cache_store", None)
    if cache_store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile is not cached.",
        )
    return cast(RiotCacheStore, cache_store)


async def _get_active_cached_riot_entry(
    *,
    cache_store: RiotCacheStore,
    base_url: str,
    path: str,
    params: dict[str, int | str | None] | None,
) -> tuple[RiotCacheEntry, str] | None:
    cache_key = build_riot_cache_key(
        method="GET",
        base_url=base_url,
        path=path,
        params=params,
    )
    try:
        entry = await cache_store.get(cache_key.cache_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Riot cache read failed.",
        ) from exc
    if entry is None:
        return None
    cache_status = entry.status_at()
    if cache_status is None:
        return None
    return entry, cache_status


def _account_v1_path(params: ProfileFetchParams) -> str:
    return (
        "/riot/account/v1/accounts/by-riot-id/"
        f"{_path_segment(params.game_name)}/{_path_segment(params.tag_line)}"
    )


def _summoner_v4_path(puuid: str) -> str:
    return f"/lol/summoner/v4/summoners/by-puuid/{_path_segment(puuid)}"


def _match_ids_v5_path(puuid: str) -> str:
    return f"/lol/match/v5/matches/by-puuid/{_path_segment(puuid)}/ids"


def _parse_riot_id(riot_id: str) -> tuple[str, str]:
    if riot_id.count("#") != 1:
        raise HTTPException(
            status_code=422,
            detail="riot_id must use gameName#tagLine format.",
        )
    game_name, tag_line = (part.strip() for part in riot_id.split("#", maxsplit=1))
    if not game_name or not tag_line:
        raise HTTPException(
            status_code=422,
            detail="riot_id must include a non-empty gameName and tagLine.",
        )
    return game_name, tag_line


def _puuid(account: dict[str, Any]) -> str:
    puuid = account.get("puuid")
    if not isinstance(puuid, str) or not puuid:
        msg = "Account-V1 response did not include a PUUID."
        raise ValueError(msg)
    return puuid


def _path_segment(value: str) -> str:
    return quote(value, safe="")
