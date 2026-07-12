from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from league_api.api.query import add_accept_query_header, parse_query_json
from league_api.api.routes.jobs import (
    _estimate_job,
    _job_payload,
    _job_stage,
    get_job_queue,
    get_job_store,
)
from league_api.api.routes.riot import RiotClientDependency
from league_api.jobs.models import (
    JobEstimate,
    JobProgress,
    JobRecord,
    JobStatus,
    JobType,
    JobWait,
    ProfileFetchParams,
    ProfileFetchResult,
)
from league_api.jobs.profile import MATCH_ID_PAGE_SIZE
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

DEFAULT_PROFILE_MATCH_LIMIT = 15
MAX_PROFILE_MATCH_LIMIT = 50


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


class ProfileViewQueryRequest(ProfileCacheQueryRequest):
    match_start: int = Field(default=0, ge=0)
    match_limit: int = Field(
        default=DEFAULT_PROFILE_MATCH_LIMIT,
        ge=1,
        le=MAX_PROFILE_MATCH_LIMIT,
    )


class ProfileIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    riot_id: str
    game_name: str
    tag_line: str
    puuid: str | None = None


class ProfileCacheStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str | None = None
    summoner: str | None = None
    match_ids: str | None = None


class ProfileMatchPaginationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int
    limit: int
    returned: int
    total: int
    has_more: bool
    next_start: int | None = None


class ProfileViewState(StrEnum):
    MISSING = "missing"
    POPULATING = "populating"
    READY = "ready"
    REFRESHING = "refreshing"
    FAILED = "failed"


class ProfileViewOperation(StrEnum):
    INITIAL_POPULATION = "initial_population"
    REFRESH = "refresh"


class ProfileStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ProfileViewState
    operation: ProfileViewOperation | None = None
    message: str
    stage: str | None = None
    stage_description: str | None = None


class ProfileDataSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_available: bool
    summoner_available: bool
    match_ids_available: bool
    match_details_available: bool
    unique_match_ids: int
    matches_available: int
    matches_pending: int
    last_updated_at: datetime | None = None
    refresh_after: datetime | None = None


class ProfileProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counters: JobProgress
    estimate: JobEstimate
    current_wait: JobWait | None = None


class ProfileDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache: ProfileCacheStatusResponse
    active_job: dict[str, Any] | None = None
    latest_job: dict[str, Any] | None = None


class ProfileViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProfileIdentityResponse
    status: ProfileStatusResponse
    data_summary: ProfileDataSummaryResponse
    progress: ProfileProgressResponse | None = None
    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    matches: list[dict[str, Any]]
    matches_pagination: ProfileMatchPaginationResponse
    diagnostics: ProfileDiagnosticsResponse


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
    active_job = await _find_matching_profile_job(
        store,
        params,
        statuses={JobStatus.QUEUED, JobStatus.RUNNING},
    )
    if active_job is not None:
        active_params = cast(ProfileFetchParams, active_job.params)
        if active_job.status is JobStatus.QUEUED:
            priority = (
                PROFILE_MATCH_DETAILS_PRIORITY
                if active_params.match_ids is not None
                else PROFILE_FETCH_PRIORITY
            )
            await job_queue.enqueue(active_job.job_id, priority=priority)
        return ProfileFetchResponse(
            job_id=active_job.job_id,
            job_type=active_job.job_type,
            status=active_job.status,
            identity_status="already_running",
            account=active_params.account,
            summoner=active_params.summoner,
            match_ids=active_params.match_ids,
        )

    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    identity_status = "queued"

    try:
        async with riot_client:
            account = await _fetch_account_without_wait(riot_client, params)
            summoner = await _fetch_summoner_without_wait(riot_client, params, _puuid(account))
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
    "/by-riot-id/{gameName}/{tagLine}",
    response_model=ProfileViewResponse,
    summary="Get a composed profile view by Riot ID",
)
async def get_profile_by_riot_id(
    request: Request,
    response: Response,
    store: Annotated[JobStore, Depends(get_job_store)],
    game_name: Annotated[str, Path(alias="gameName", min_length=1, description="Game name.")],
    tag_line: Annotated[str, Path(alias="tagLine", min_length=1, description="Tag line.")],
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
    match_start: Annotated[
        int,
        Query(ge=0, description="Zero-based match summary offset."),
    ] = 0,
    match_limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PROFILE_MATCH_LIMIT,
            description="Number of compact match summaries to return.",
        ),
    ] = DEFAULT_PROFILE_MATCH_LIMIT,
) -> ProfileViewResponse:
    add_accept_query_header(response)
    return await _get_profile_view(
        request=request,
        store=store,
        game_name=game_name,
        tag_line=tag_line,
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
        match_start=match_start,
        match_limit=match_limit,
    )


@router.api_route(
    "/by-riot-id",
    methods=["QUERY"],
    response_model=ProfileViewResponse,
    summary="Query a composed profile view by Riot ID",
)
async def query_profile_by_riot_id(
    request: Request,
    response: Response,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> ProfileViewResponse:
    query = await parse_query_json(request, ProfileViewQueryRequest)
    game_name, tag_line = _parse_riot_id(query.riot_id)
    add_accept_query_header(response)
    return await _get_profile_view(
        request=request,
        store=store,
        game_name=game_name,
        tag_line=tag_line,
        account_regional_route=query.account_regional_route,
        platform_route=query.platform_route,
        regional_route=query.regional_route,
        match_start=query.match_start,
        match_limit=query.match_limit,
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


async def _get_profile_view(
    *,
    request: Request,
    store: JobStore,
    game_name: str,
    tag_line: str,
    account_regional_route: RiotAccountRegionalRoute,
    platform_route: RiotPlatformRoute,
    regional_route: RiotRegionalRoute,
    match_start: int,
    match_limit: int,
) -> ProfileViewResponse:
    params = ProfileFetchParams(
        game_name=game_name.strip(),
        tag_line=tag_line.strip(),
        account_regional_route=account_regional_route,
        platform_route=platform_route,
        regional_route=regional_route,
    )
    active_job = await _find_matching_profile_job(
        store,
        params,
        statuses={JobStatus.QUEUED, JobStatus.RUNNING},
        include_events=True,
        include_result=True,
    )
    terminal_jobs = await _find_matching_profile_jobs(
        store,
        params,
        statuses={JobStatus.SUCCEEDED, JobStatus.FAILED},
        include_events=True,
        include_result=True,
        limit=50,
    )
    latest_job = terminal_jobs[0] if terminal_jobs else None
    latest_success_job = next(
        (
            job
            for job in terminal_jobs
            if job.status is JobStatus.SUCCEEDED and isinstance(job.result, ProfileFetchResult)
        ),
        None,
    )

    fallback_account = (
        _job_account(active_job) or _job_account(latest_success_job) or _job_account(latest_job)
    )
    fallback_puuid = _safe_puuid(fallback_account)
    (
        cache_account,
        cache_summoner,
        cache_match_ids,
        cache_status,
        cache_entries,
    ) = await _get_profile_cache_snapshot(
        request=request,
        params=params,
        fallback_puuid=fallback_puuid,
    )

    account = cache_account or fallback_account
    puuid = _safe_puuid(account) or fallback_puuid
    summoner = cache_summoner or _job_summoner(active_job) or _job_summoner(latest_success_job)
    match_ids = (
        cache_match_ids
        if cache_match_ids is not None
        else _job_match_ids(active_job) or _job_match_ids(latest_success_job)
    )
    match_source_job = (
        active_job
        if active_job is not None and isinstance(active_job.result, ProfileFetchResult)
        else None
    )
    all_matches = _compact_match_summaries(match_source_job or latest_success_job, puuid=puuid)
    matches, matches_pagination = _paginate_profile_matches(
        all_matches,
        start=match_start,
        limit=match_limit,
    )
    last_updated_at = max(entry.fetched_at for entry in cache_entries) if cache_entries else None
    refresh_after = min(entry.expires_at for entry in cache_entries) if cache_entries else None
    unique_match_count = len(set(match_ids or []))
    matches_available = len(all_matches)
    data_summary = ProfileDataSummaryResponse(
        account_available=account is not None,
        summoner_available=summoner is not None,
        match_ids_available=match_ids is not None,
        match_details_available=match_ids is not None and matches_available >= unique_match_count,
        unique_match_ids=unique_match_count,
        matches_available=matches_available,
        matches_pending=max(unique_match_count - matches_available, 0),
        last_updated_at=last_updated_at,
        refresh_after=refresh_after,
    )
    profile_status = _profile_status(
        active_job=active_job,
        latest_job=latest_job,
        latest_success_job=latest_success_job,
        data_summary=data_summary,
        cache_status=cache_status,
        cache_entries=cache_entries,
    )

    return ProfileViewResponse(
        profile=ProfileIdentityResponse(
            riot_id=params.riot_id,
            game_name=params.game_name,
            tag_line=params.tag_line,
            puuid=puuid,
        ),
        status=profile_status,
        data_summary=data_summary,
        progress=_profile_progress(active_job),
        account=account,
        summoner=summoner,
        match_ids=match_ids,
        matches=matches,
        matches_pagination=matches_pagination,
        diagnostics=ProfileDiagnosticsResponse(
            cache=cache_status,
            active_job=_profile_job_summary(active_job),
            latest_job=_profile_job_summary(latest_job),
        ),
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
    match_ids_cached = None
    for count in _cached_match_id_counts(params):
        match_ids_cached = await _get_active_cached_riot_entry(
            cache_store=cache_store,
            base_url=get_regional_base_url(params.regional_route),
            path=_match_ids_v5_path(puuid),
            params={"start": 0, "count": count},
        )
        if match_ids_cached is not None:
            break
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


async def _find_matching_profile_job(
    store: JobStore,
    params: ProfileFetchParams,
    *,
    statuses: set[JobStatus],
    include_events: bool = False,
    include_result: bool = False,
) -> JobRecord | None:
    jobs = await _find_matching_profile_jobs(
        store,
        params,
        statuses=statuses,
        include_events=include_events,
        include_result=include_result,
        limit=20,
    )
    return jobs[0] if jobs else None


async def _find_matching_profile_jobs(
    store: JobStore,
    params: ProfileFetchParams,
    *,
    statuses: set[JobStatus],
    include_events: bool = False,
    include_result: bool = False,
    limit: int = 20,
) -> list[JobRecord]:
    page = await store.list_jobs_page(
        statuses=statuses,
        job_type=JobType.PROFILE_FETCH,
        riot_id=params.riot_id,
        limit=limit,
        include_events=include_events,
        include_result=include_result,
    )
    return [
        job
        for job in page.jobs
        if isinstance(job.params, ProfileFetchParams) and _same_profile_params(job.params, params)
    ]


async def _get_profile_cache_snapshot(
    *,
    request: Request,
    params: ProfileFetchParams,
    fallback_puuid: str | None,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[str] | None,
    ProfileCacheStatusResponse,
    list[RiotCacheEntry],
]:
    cache_store = _optional_riot_cache_store(request)
    cache_status = ProfileCacheStatusResponse()
    cache_entries: list[RiotCacheEntry] = []
    if cache_store is None:
        return None, None, None, cache_status, cache_entries

    account: dict[str, Any] | None = None
    account_cached = await _get_active_cached_riot_entry(
        cache_store=cache_store,
        base_url=get_account_regional_base_url(params.account_regional_route),
        path=_account_v1_path(params),
        params=None,
    )
    if account_cached is not None:
        account_entry, cache_status.account = account_cached
        cache_entries.append(account_entry)
        if not isinstance(account_entry.payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cached Account-V1 profile data is invalid.",
            )
        account = account_entry.payload

    puuid = _safe_puuid(account) or fallback_puuid
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None
    if puuid is None:
        return account, summoner, match_ids, cache_status, cache_entries

    summoner_cached = await _get_active_cached_riot_entry(
        cache_store=cache_store,
        base_url=get_platform_base_url(params.platform_route),
        path=_summoner_v4_path(puuid),
        params=None,
    )
    if summoner_cached is not None:
        summoner_entry, cache_status.summoner = summoner_cached
        cache_entries.append(summoner_entry)
        if not isinstance(summoner_entry.payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Cached Summoner-V4 profile data is invalid.",
            )
        summoner = summoner_entry.payload

    match_ids_cached = None
    for count in _cached_match_id_counts(params):
        match_ids_cached = await _get_active_cached_riot_entry(
            cache_store=cache_store,
            base_url=get_regional_base_url(params.regional_route),
            path=_match_ids_v5_path(puuid),
            params={"start": 0, "count": count},
        )
        if match_ids_cached is not None:
            break
    if match_ids_cached is not None:
        match_ids_entry, cache_status.match_ids = match_ids_cached
        cache_entries.append(match_ids_entry)
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

    return account, summoner, match_ids, cache_status, cache_entries


def _profile_status(
    *,
    active_job: JobRecord | None,
    latest_job: JobRecord | None,
    latest_success_job: JobRecord | None,
    data_summary: ProfileDataSummaryResponse,
    cache_status: ProfileCacheStatusResponse,
    cache_entries: list[RiotCacheEntry],
) -> ProfileStatusResponse:
    if active_job is not None:
        stage, description = _job_stage(active_job)
        if latest_success_job is None:
            return ProfileStatusResponse(
                state=ProfileViewState.POPULATING,
                operation=ProfileViewOperation.INITIAL_POPULATION,
                message="Populating this profile for the first time.",
                stage=stage,
                stage_description=description,
            )
        return ProfileStatusResponse(
            state=ProfileViewState.REFRESHING,
            operation=ProfileViewOperation.REFRESH,
            message="Refreshing this profile while existing data remains available.",
            stage=stage,
            stage_description=description,
        )

    has_usable_data = data_summary.account_available
    if latest_job is not None and latest_job.status is JobStatus.FAILED and not has_usable_data:
        stage, description = _job_stage(latest_job)
        return ProfileStatusResponse(
            state=ProfileViewState.FAILED,
            message="The profile could not be populated.",
            stage=stage,
            stage_description=description,
        )
    if not has_usable_data:
        return ProfileStatusResponse(
            state=ProfileViewState.MISSING,
            message="No profile data has been populated yet.",
        )

    cache_values = (cache_status.account, cache_status.summoner, cache_status.match_ids)
    is_stale = (
        not cache_entries
        or "stale" in cache_values
        or not data_summary.summoner_available
        or not data_summary.match_ids_available
        or any(value != "hit" for value in cache_values)
    )
    message = (
        "Profile data is available but due for a refresh."
        if is_stale
        else "Profile data is populated and current."
    )
    return ProfileStatusResponse(state=ProfileViewState.READY, message=message)


def _profile_progress(job: JobRecord | None) -> ProfileProgressResponse | None:
    if job is None:
        return None
    return ProfileProgressResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        counters=job.progress,
        estimate=_estimate_job(job),
        current_wait=job.current_wait,
    )


def _cached_match_id_counts(params: ProfileFetchParams) -> list[int]:
    if params.match_count is None:
        return [MATCH_ID_PAGE_SIZE, 20]
    return [params.match_count]


def _same_profile_params(left: ProfileFetchParams, right: ProfileFetchParams) -> bool:
    return (
        left.game_name.strip().casefold() == right.game_name.strip().casefold()
        and left.tag_line.strip().casefold() == right.tag_line.strip().casefold()
        and left.account_regional_route == right.account_regional_route
        and left.platform_route == right.platform_route
        and left.regional_route == right.regional_route
        and left.match_count == right.match_count
    )


def _profile_job_summary(job: JobRecord | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return _job_payload(job, verbose=True, include_events=True, include_result=False)


def _job_account(job: JobRecord | None) -> dict[str, Any] | None:
    if job is None or not isinstance(job.params, ProfileFetchParams):
        return None
    if isinstance(job.result, ProfileFetchResult):
        return job.result.account
    return job.params.account


def _job_summoner(job: JobRecord | None) -> dict[str, Any] | None:
    if job is None or not isinstance(job.params, ProfileFetchParams):
        return None
    if isinstance(job.result, ProfileFetchResult):
        return job.result.summoner
    return job.params.summoner


def _job_match_ids(job: JobRecord | None) -> list[str] | None:
    if job is None or not isinstance(job.params, ProfileFetchParams):
        return None
    if isinstance(job.result, ProfileFetchResult):
        return job.result.match_ids
    return job.params.match_ids


def _compact_match_summaries(job: JobRecord | None, *, puuid: str | None) -> list[dict[str, Any]]:
    if job is None or not isinstance(job.result, ProfileFetchResult):
        return []
    summaries: list[dict[str, Any]] = []
    for match_id in job.result.match_ids:
        match = job.result.matches.get(match_id)
        if not isinstance(match, dict):
            continue
        info = match.get("info")
        if not isinstance(info, dict):
            continue
        participant = _participant_for_match(info, puuid=puuid)
        summary: dict[str, Any] = {
            "match_id": match_id,
            "game_creation": info.get("gameCreation"),
            "game_duration": info.get("gameDuration"),
            "game_mode": info.get("gameMode"),
            "queue_id": info.get("queueId"),
        }
        if participant is not None:
            summary.update(
                {
                    "champion_id": participant.get("championId"),
                    "champion_name": participant.get("championName"),
                    "win": participant.get("win"),
                    "kills": participant.get("kills"),
                    "deaths": participant.get("deaths"),
                    "assists": participant.get("assists"),
                    "lane": participant.get("lane"),
                    "team_position": participant.get("teamPosition"),
                }
            )
        summaries.append(summary)
    return summaries


def _paginate_profile_matches(
    matches: list[dict[str, Any]],
    *,
    start: int,
    limit: int,
) -> tuple[list[dict[str, Any]], ProfileMatchPaginationResponse]:
    page = matches[start : start + limit]
    next_start = start + len(page)
    has_more = next_start < len(matches)
    return page, ProfileMatchPaginationResponse(
        start=start,
        limit=limit,
        returned=len(page),
        total=len(matches),
        has_more=has_more,
        next_start=next_start if has_more else None,
    )


def _participant_for_match(info: dict[str, Any], *, puuid: str | None) -> dict[str, Any] | None:
    participants = info.get("participants")
    if not isinstance(participants, list):
        return None
    if puuid is None:
        return None
    for participant in participants:
        if isinstance(participant, dict) and participant.get("puuid") == puuid:
            return participant
    return None


def _optional_riot_cache_store(request: Request) -> RiotCacheStore | None:
    return cast(RiotCacheStore | None, getattr(request.app.state, "riot_cache_store", None))


def _safe_puuid(account: dict[str, Any] | None) -> str | None:
    if account is None:
        return None
    puuid = account.get("puuid")
    return puuid if isinstance(puuid, str) and puuid else None


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
        params={"start": 0, "count": params.match_count or MATCH_ID_PAGE_SIZE},
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
