import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from league_api.api.query import add_accept_query_header, parse_query_json
from league_api.core.auth import require_operator_token
from league_api.core.config import Settings, get_settings
from league_api.jobs.models import (
    JobDetails,
    JobEstimate,
    JobEvent,
    JobProgress,
    JobRecord,
    JobStatus,
    JobType,
    JobWait,
    LadderFetchMode,
    LadderIngestionParams,
    LadderIngestionResult,
    LadderJobDetails,
    LadderPlayersJobDetails,
    LadderPlayersParams,
    LadderPlayersResult,
    LadderType,
    ProfileFetchParams,
    ProfileFetchResult,
    ProfileJobDetails,
    RankedDivision,
    RankedTier,
)
from league_api.jobs.queue import (
    LADDER_INGESTION_PRIORITY,
    LADDER_PLAYERS_PRIORITY,
    InMemoryJobQueue,
)
from league_api.jobs.store import JobListCursor, JobStore
from league_api.riot.queues import LeagueQueue, league_queue_label
from league_api.riot.routing import RiotAccountRegionalRoute, RiotPlatformRoute, RiotRegionalRoute

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    details: JobDetails
    progress: dict[str, Any]
    estimate: JobEstimate
    current_wait: JobWait | None
    events: list[JobEvent]


class JobSucceededResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    details: JobDetails
    summary: JobProgress
    estimate: JobEstimate
    player_puuids: list[str]
    match_ids: list[str]
    matches: dict[str, dict[str, Any]]
    errors: list[Any]


class JobStatusListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    running_only: bool
    verbose: bool
    include_events: bool
    include_result: bool
    limit: int
    next_cursor: str | None
    has_more: bool
    jobs: list[dict[str, Any]]


class JobStatusQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    running_only: bool = True
    verbose: bool = False
    include_events: bool = False
    include_result: bool = False
    status: list[JobStatus] | None = None
    job_type: JobType | None = None
    riot_id: str | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


def get_job_store(request: Request) -> JobStore:
    return cast(JobStore, request.app.state.job_store)


def get_job_queue(request: Request) -> InMemoryJobQueue:
    return cast(InMemoryJobQueue, request.app.state.job_queue)


@router.post(
    "/ingestion/ladder",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a ladder ingestion job",
    dependencies=[Depends(require_operator_token)],
)
async def start_ladder_ingestion_job(
    store: Annotated[JobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for League-V4 ladder data."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5 match data."),
    ] = RiotRegionalRoute.SEA,
    queue: Annotated[
        LeagueQueue,
        Query(description="Ranked League-V4 queue."),
    ] = LeagueQueue.RANKED_SOLO_5X5,
    ladder: Annotated[
        LadderType,
        Query(description="Ladder source to ingest. Only challenger is supported in this stage."),
    ] = LadderType.CHALLENGER,
    match_count: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobStatusResponse:
    params = LadderIngestionParams(
        platform_route=platform_route,
        regional_route=regional_route,
        queue=queue,
        ladder=ladder,
        match_count=match_count,
    )
    job = await store.create_job(job_type=JobType.LADDER_INGESTION, params=params)
    await job_queue.enqueue(job.job_id, priority=LADDER_INGESTION_PRIORITY)
    return _status_response(job)


@router.post(
    "/ingestion/ladder-players",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fetch and persist ranked ladder players",
    dependencies=[Depends(require_operator_token)],
)
async def start_ladder_players_job(
    store: Annotated[JobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1,
    account_regional_route: RiotAccountRegionalRoute = RiotAccountRegionalRoute.ASIA,
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA,
    queue: LeagueQueue = LeagueQueue.RANKED_SOLO_5X5,
    tier: RankedTier = RankedTier.CHALLENGER,
    division: RankedDivision | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    mode: LadderFetchMode = LadderFetchMode.LADDER_ONLY,
) -> JobStatusResponse:
    try:
        params = LadderPlayersParams(
            platform_route=platform_route,
            account_regional_route=account_regional_route,
            regional_route=regional_route,
            queue=queue,
            tier=tier,
            division=division,
            page=page,
            mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    await job_queue.enqueue(job.job_id, priority=LADDER_PLAYERS_PRIORITY)
    return _status_response(job)


@router.get(
    "/status",
    response_model=JobStatusListResponse,
    summary="List job status",
)
async def list_job_status(
    response: Response,
    store: Annotated[JobStore, Depends(get_job_store)],
    running_only: Annotated[
        bool,
        Query(description="Only include queued and running jobs."),
    ] = True,
    verbose: Annotated[
        bool,
        Query(description="Include params, errors, and last event details for each job."),
    ] = False,
    include_events: Annotated[
        bool,
        Query(description="Include each job's retained event history."),
    ] = False,
    include_result: Annotated[
        bool,
        Query(description="Include completed job results when available."),
    ] = False,
    job_statuses: Annotated[
        list[JobStatus] | None,
        Query(alias="status", description="Filter by one or more job statuses."),
    ] = None,
    job_type: Annotated[
        JobType | None,
        Query(description="Filter by job type."),
    ] = None,
    riot_id: Annotated[
        str | None,
        Query(description="Filter profile jobs by Riot ID in gameName#tagLine format."),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum number of jobs to return."),
    ] = 50,
    cursor: Annotated[
        str | None,
        Query(description="Opaque pagination cursor from a previous response."),
    ] = None,
) -> JobStatusListResponse:
    add_accept_query_header(response)
    return await _list_job_status(
        store=store,
        running_only=running_only,
        verbose=verbose,
        include_events=include_events,
        include_result=include_result,
        job_statuses=set(job_statuses) if job_statuses is not None else None,
        job_type=job_type,
        riot_id=riot_id,
        limit=limit,
        cursor=cursor,
    )


@router.api_route(
    "/status",
    methods=["QUERY"],
    response_model=JobStatusListResponse,
    summary="Query job status",
)
async def query_job_status(
    request: Request,
    response: Response,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> JobStatusListResponse:
    query = await parse_query_json(request, JobStatusQueryRequest)
    add_accept_query_header(response)
    return await _list_job_status(
        store=store,
        running_only=query.running_only,
        verbose=query.verbose,
        include_events=query.include_events,
        include_result=query.include_result,
        job_statuses=set(query.status) if query.status is not None else None,
        job_type=query.job_type,
        riot_id=query.riot_id,
        limit=query.limit,
        cursor=query.cursor,
    )


async def _list_job_status(
    *,
    store: JobStore,
    running_only: bool,
    verbose: bool,
    include_events: bool,
    include_result: bool,
    job_statuses: set[JobStatus] | None = None,
    job_type: JobType | None = None,
    riot_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> JobStatusListResponse:
    statuses = (
        job_statuses
        if job_statuses is not None
        else {JobStatus.QUEUED, JobStatus.RUNNING}
        if running_only
        else None
    )
    page = await store.list_jobs_page(
        statuses=statuses,
        job_type=job_type,
        riot_id=riot_id,
        limit=limit,
        cursor=_decode_cursor(cursor) if cursor is not None else None,
        include_events=verbose or include_events,
        include_result=include_result,
    )
    generated_at = datetime.now(UTC)
    return JobStatusListResponse(
        generated_at=generated_at,
        running_only=running_only,
        verbose=verbose,
        include_events=include_events,
        include_result=include_result,
        limit=limit,
        next_cursor=_encode_cursor(page.next_cursor),
        has_more=page.has_more,
        jobs=[
            _job_payload(
                job,
                now=generated_at,
                verbose=verbose,
                include_events=include_events,
                include_result=include_result,
            )
            for job in page.jobs
        ],
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
)
async def get_job(
    job_id: str,
    store: Annotated[JobStore, Depends(get_job_store)],
    include_events: Annotated[
        bool,
        Query(description="Include retained event history for the job."),
    ] = True,
) -> JobStatusResponse:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _status_response(job, include_events=include_events)


@router.get(
    "/{job_id}/result",
    response_model=None,
    summary="Get job result",
)
async def get_job_result(
    job_id: str,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> dict[str, Any] | JSONResponse:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job.job_id,
                "status": job.status,
                "message": "Job is still queued or running.",
                "details": _job_details(job).model_dump(mode="json"),
                "progress": _progress_payload(job),
                "estimate": _estimate_job(job).model_dump(mode="json"),
                "current_wait": (
                    job.current_wait.model_dump(mode="json")
                    if job.current_wait is not None
                    else None
                ),
                "events": [event.model_dump(mode="json") for event in job.events],
            },
        )

    if job.status is JobStatus.FAILED:
        return _job_payload(job, verbose=True, include_events=True, include_result=True)

    if job.result is None:
        msg = "Completed job is missing a result."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

    return _success_result(job)


def _status_response(job: JobRecord, *, include_events: bool = True) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        details=_job_details(job),
        progress=_progress_payload(job),
        estimate=_estimate_job(job),
        current_wait=job.current_wait,
        events=job.events if include_events else [],
    )


def _success_result(job: JobRecord) -> dict[str, Any]:
    if job.result is None:
        msg = "Completed job is missing a result."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "status": JobStatus.SUCCEEDED,
        "details": _job_details(job).model_dump(mode="json"),
        "summary": job.result.summary.model_dump(mode="json"),
        "estimate": _estimate_job(job).model_dump(mode="json"),
        "errors": [error.model_dump(mode="json") for error in job.result.errors],
    }
    if isinstance(job.result, (LadderIngestionResult, LadderPlayersResult, ProfileFetchResult)):
        payload["match_ids"] = job.result.match_ids
    if isinstance(job.result, (LadderIngestionResult, ProfileFetchResult)):
        payload["matches"] = job.result.matches
    if isinstance(job.result, (LadderIngestionResult, LadderPlayersResult)):
        payload["player_puuids"] = job.result.player_puuids
    if isinstance(job.result, ProfileFetchResult):
        payload["account"] = job.result.account
        payload["summoner"] = job.result.summoner
    return payload


def _encode_cursor(cursor: JobListCursor | None) -> str | None:
    if cursor is None:
        return None
    payload = {
        "created_at": cursor.created_at.isoformat(),
        "job_id": cursor.job_id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> JobListCursor:
    try:
        padded_cursor = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded_cursor.encode()).decode())
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        job_id = str(payload["job_id"])
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job status cursor.",
        ) from exc
    if not job_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job status cursor.",
        )
    return JobListCursor(created_at=created_at, job_id=job_id)


def _job_payload(
    job: JobRecord,
    *,
    now: datetime | None = None,
    verbose: bool,
    include_events: bool,
    include_result: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "details": _job_details(job).model_dump(mode="json"),
        "progress": _progress_payload(job),
        "estimate": _estimate_job(job, now=now).model_dump(mode="json"),
        "current_wait": (
            job.current_wait.model_dump(mode="json") if job.current_wait is not None else None
        ),
    }

    if verbose:
        latest_event = job.events[-1] if job.events else None
        payload["params"] = job.params.model_dump(mode="json")
        payload["error"] = job.error.model_dump(mode="json") if job.error is not None else None
        payload["last_event"] = (
            latest_event.model_dump(mode="json") if latest_event is not None else None
        )

    if include_events:
        payload["events"] = [event.model_dump(mode="json") for event in job.events]

    if include_result:
        payload["result"] = job.result.model_dump(mode="json") if job.result is not None else None

    return payload


def _progress_payload(job: JobRecord) -> dict[str, Any]:
    payload = job.progress.model_dump(mode="json")
    if job.job_type is not JobType.LADDER_PLAYERS:
        for field in (
            "identities_resolved",
            "identities_reused",
            "identities_unresolved",
            "current_player_puuid",
            "phase",
            "current_match_id_start",
            "match_id_pages_attempted",
            "match_id_pages_failed",
            "match_id_pages_retried",
            "duplicate_match_references",
            "match_details_reused",
        ):
            payload.pop(field, None)
    return payload


def _job_details(job: JobRecord) -> JobDetails:
    if isinstance(job.params, LadderPlayersParams):
        return LadderPlayersJobDetails(
            source="league_v4_ranked_players",
            platform_route=job.params.platform_route,
            regional_route=job.params.regional_route,
            queue=job.params.queue,
            queue_label=_queue_label(job.params.queue),
            tier=job.params.tier,
            division=job.params.division,
            page=job.params.page,
            player_count=job.progress.players_discovered,
            identities_resolved=job.progress.identities_resolved,
            identities_reused=job.progress.identities_reused,
            identities_unresolved=job.progress.identities_unresolved,
            mode=job.params.mode,
        )
    if isinstance(job.params, LadderIngestionParams):
        return LadderJobDetails(
            source=_job_source(job),
            platform_route=job.params.platform_route,
            regional_route=job.params.regional_route,
            queue=job.params.queue,
            queue_label=_queue_label(job.params.queue),
            ladder=job.params.ladder,
            tier=_tier_for_ladder(job.params.ladder),
            division=_division_for_ladder(job.params.ladder),
            match_count_per_player=job.params.match_count,
            player_count=job.progress.players_discovered,
            match_id_request_count=job.progress.players_discovered,
            match_detail_request_count=job.progress.unique_match_ids,
        )

    result = job.result if isinstance(job.result, ProfileFetchResult) else None
    account = job.params.account or (result.account if result is not None else {})
    summoner = job.params.summoner or (result.summoner if result is not None else {})
    puuid = account.get("puuid") or summoner.get("puuid")
    return ProfileJobDetails(
        source=_job_source(job),
        riot_id=job.params.riot_id,
        game_name=job.params.game_name,
        tag_line=job.params.tag_line,
        puuid=puuid if isinstance(puuid, str) else None,
        account_regional_route=job.params.account_regional_route,
        platform_route=job.params.platform_route,
        regional_route=job.params.regional_route,
        match_count=job.params.match_count,
        match_id_request_count=1,
        match_id_page_request_count=job.progress.match_id_pages_fetched,
        match_id_pages_with_results=job.progress.match_id_pages_with_results,
        match_detail_request_count=job.progress.unique_match_ids,
    )


def _job_source(job: JobRecord) -> str:
    if job.job_type is JobType.LADDER_PLAYERS:
        return "league_v4_ranked_players"
    if job.job_type is JobType.LADDER_INGESTION:
        return "league_v4_apex_ladder"
    if job.job_type is JobType.PROFILE_FETCH:
        return "profile_fetch"
    return "unknown"


def _queue_label(queue: str | LeagueQueue) -> str:
    return league_queue_label(queue)


def _tier_for_ladder(ladder: LadderType) -> str:
    return ladder.value.upper()


def _division_for_ladder(ladder: LadderType) -> str | None:
    if ladder is LadderType.CHALLENGER:
        return None
    return None


def _estimate_job(
    job: JobRecord,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> JobEstimate:
    snapshot_at = now or datetime.now(UTC)
    resolved_settings = settings or get_settings()
    stage, description = _job_stage(job)
    requests_completed, requests_total = _request_counts(job)
    requests_remaining = (
        max(requests_total - requests_completed, 0) if requests_total is not None else None
    )
    percent_complete = (
        round((requests_completed / requests_total) * 100, 1)
        if requests_total is not None and requests_total > 0
        else None
    )
    average_seconds_per_request: float | None = None
    rate_limit_seconds_remaining: float | None = None
    estimated_seconds_remaining: float | None = None
    estimated_completed_at: datetime | None = None

    if job.status is JobStatus.SUCCEEDED:
        estimated_seconds_remaining = 0.0
        estimated_completed_at = job.finished_at
        rate_limit_seconds_remaining = 0.0
        percent_complete = 100.0 if requests_total is not None else percent_complete
    elif job.status is JobStatus.RUNNING:
        if job.started_at is not None and requests_completed > 0:
            elapsed_seconds = max((snapshot_at - job.started_at).total_seconds(), 0.0)
            average_seconds_per_request = elapsed_seconds / requests_completed
        wait_seconds_remaining = _wait_seconds_remaining(job, snapshot_at)
        if requests_remaining is not None:
            rate_limit_seconds_remaining = _rate_limit_seconds_for_requests(
                requests_remaining,
                settings=resolved_settings,
            )
        observed_seconds_remaining = (
            requests_remaining * average_seconds_per_request
            if requests_remaining is not None and average_seconds_per_request is not None
            else None
        )
        seconds_after_wait = max(
            value
            for value in (observed_seconds_remaining, rate_limit_seconds_remaining, 0.0)
            if value is not None
        )
        estimated_seconds_remaining = wait_seconds_remaining + seconds_after_wait
        if estimated_seconds_remaining is not None:
            estimated_completed_at = snapshot_at + timedelta(seconds=estimated_seconds_remaining)

    return JobEstimate(
        stage=stage,
        description=description,
        current_path=_current_path(job),
        requests_completed=requests_completed,
        requests_total=requests_total,
        requests_remaining=requests_remaining,
        percent_complete=percent_complete,
        average_seconds_per_request=(
            round(average_seconds_per_request, 3)
            if average_seconds_per_request is not None
            else None
        ),
        rate_limit_seconds_remaining=(
            round(rate_limit_seconds_remaining, 3)
            if rate_limit_seconds_remaining is not None
            else None
        ),
        rate_limit_label=_rate_limit_label(resolved_settings),
        estimated_seconds_remaining=(
            round(estimated_seconds_remaining, 3)
            if estimated_seconds_remaining is not None
            else None
        ),
        estimated_completed_at=estimated_completed_at,
    )


def _job_stage(job: JobRecord) -> tuple[str, str]:
    if job.status is JobStatus.QUEUED:
        if job.job_type is JobType.PROFILE_FETCH:
            return "queued", "Waiting for the job worker to start profile fetching."
        return "queued", "Waiting for the job worker to start ladder ingestion."
    if job.status is JobStatus.FAILED:
        stage = job.error.stage if job.error is not None and job.error.stage else "failed"
        return stage, "Job failed before completion."
    if job.status is JobStatus.SUCCEEDED:
        return "completed", "Job completed successfully."
    if job.current_wait is not None:
        return job.current_wait.stage or "rate_limit_wait", job.current_wait.message

    if isinstance(job.params, LadderPlayersParams):
        progress = job.progress
        if progress.players_discovered == 0:
            return "ladder", f"Fetching {job.params.tier.value} ladder players."
        if progress.players_processed < progress.players_discovered:
            return (
                "account",
                f"Resolving Riot ID {progress.players_processed + 1} "
                f"of {progress.players_discovered}.",
            )
        if progress.phase == "match_id_discovery":
            return (
                "match_ids",
                f"Discovering all match-ID pages at start={progress.current_match_id_start or 0}.",
            )
        if progress.phase == "match_id_persistence":
            return "match_id_persistence", "Deduplicating and persisting match references."
        if progress.phase == "match_details":
            return (
                "match_detail",
                f"Processing match detail {progress.matches_fetched + 1} "
                f"of {progress.unique_match_ids}.",
            )
        return "finalizing", "Persisting ranked ladder players."

    if isinstance(job.params, ProfileFetchParams):
        progress = job.progress
        if progress.players_discovered == 0:
            return "account", f"Fetching Account-V1 data for {job.params.riot_id}."
        if progress.players_processed == 0:
            return "summoner", "Fetching Summoner-V4 data for the profile PUUID."
        if progress.match_ids_discovered == 0:
            return "match_ids", "Fetching recent Match-V5 IDs for the profile."
        if progress.matches_fetched < progress.unique_match_ids:
            next_match = progress.matches_fetched + 1
            page_count = progress.match_id_pages_with_results
            page_context = (
                f" from {page_count} Match-V5 ID page{'s' if page_count != 1 else ''}"
                if page_count > 0
                else ""
            )
            return (
                "match_detail",
                (
                    f"Fetching profile match detail {next_match} "
                    f"of {progress.unique_match_ids}{page_context}."
                ),
            )
        return "finalizing", "Finishing profile fetch."

    progress = job.progress
    if progress.players_discovered == 0:
        route = job.params.platform_route
        return (
            "ladder",
            f"Fetching {job.params.ladder} {job.params.queue} ladder from {route}.",
        )
    if progress.players_processed < progress.players_discovered:
        next_player = progress.players_processed + 1
        return (
            "match_ids",
            f"Fetching match IDs for player {next_player} of {progress.players_discovered}.",
        )
    if progress.matches_fetched < progress.unique_match_ids:
        next_match = progress.matches_fetched + 1
        return (
            "match_detail",
            f"Fetching match detail {next_match} of {progress.unique_match_ids}.",
        )
    return "finalizing", "Finishing ladder ingestion."


def _request_counts(job: JobRecord) -> tuple[int, int | None]:
    progress = job.progress
    if isinstance(job.params, LadderPlayersParams):
        ladder_completed = int(progress.players_discovered > 0 or job.status is JobStatus.SUCCEEDED)
        completed = (
            ladder_completed
            + progress.players_processed
            + progress.match_id_pages_fetched
            + progress.matches_fetched
        )
        total = 1 + progress.players_discovered
        if job.params.mode is LadderFetchMode.LADDER_AND_MATCHES:
            total += progress.match_id_pages_attempted + progress.unique_match_ids
        return (max(completed, total) if job.status is JobStatus.SUCCEEDED else completed, total)
    if isinstance(job.params, ProfileFetchParams):
        account_completed = int(progress.players_discovered > 0 or job.params.account is not None)
        summoner_completed = int(progress.players_processed > 0 or job.params.summoner is not None)
        match_ids_completed = int(
            progress.match_ids_discovered > 0 or job.params.match_ids is not None
        )
        requests_completed = (
            account_completed + summoner_completed + match_ids_completed + progress.matches_fetched
        )
        match_detail_total = progress.unique_match_ids
        if job.params.match_ids is not None:
            match_detail_total = len(set(job.params.match_ids))
        requests_total = 3 + match_detail_total
        if job.status is JobStatus.SUCCEEDED:
            requests_completed = max(requests_completed, requests_total)
        return requests_completed, requests_total

    ladder_completed = int(
        progress.players_discovered > 0
        or progress.players_processed > 0
        or progress.match_ids_discovered > 0
        or progress.unique_match_ids > 0
        or progress.matches_fetched > 0
        or job.status is JobStatus.SUCCEEDED
    )
    requests_completed = ladder_completed + progress.players_processed + progress.matches_fetched
    requests_total = 1 + progress.players_discovered + progress.unique_match_ids

    if job.status is JobStatus.SUCCEEDED:
        requests_completed = max(requests_completed, requests_total)
    return requests_completed, requests_total


def _rate_limit_seconds_for_requests(request_count: int, *, settings: Settings) -> float:
    if request_count <= 0:
        return 0.0

    seconds_per_request = max(
        settings.riot_app_rate_limit_short_window_seconds
        / settings.riot_app_rate_limit_short_requests,
        settings.riot_app_rate_limit_long_window_seconds
        / settings.riot_app_rate_limit_long_requests,
    )
    return request_count * seconds_per_request


def _rate_limit_label(settings: Settings) -> str:
    return (
        f"{settings.riot_app_rate_limit_short_requests}/"
        f"{_format_seconds(settings.riot_app_rate_limit_short_window_seconds)}-"
        f"{settings.riot_app_rate_limit_long_requests}/"
        f"{_format_seconds(settings.riot_app_rate_limit_long_window_seconds)}"
    )


def _format_seconds(seconds: float) -> str:
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def _wait_seconds_remaining(job: JobRecord, now: datetime) -> float:
    if job.current_wait is None:
        return 0.0
    return max((job.current_wait.resume_at - now).total_seconds(), 0.0)


def _current_path(job: JobRecord) -> str | None:
    if job.current_wait is not None:
        return job.current_wait.path
    for event in reversed(job.events):
        if event.event_type in {"request_started", "rate_limit_wait"} and event.path is not None:
            return event.path
    return None
