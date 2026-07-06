from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

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
    LadderIngestionParams,
    LadderType,
)
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.routing import RiotPlatformRoute, RiotRegionalRoute

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
    progress: JobProgress
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
    jobs: list[dict[str, Any]]


def get_job_store(request: Request) -> InMemoryJobStore:
    return cast(InMemoryJobStore, request.app.state.job_store)


def get_job_queue(request: Request) -> InMemoryJobQueue:
    return cast(InMemoryJobQueue, request.app.state.job_queue)


@router.post(
    "/ingestion/ladder",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a ladder ingestion job",
)
async def start_ladder_ingestion_job(
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
    job_queue: Annotated[InMemoryJobQueue, Depends(get_job_queue)],
    platform_route: Annotated[
        RiotPlatformRoute,
        Query(description="Riot platform route for League-V4 ladder data."),
    ] = RiotPlatformRoute.OC1,
    regional_route: Annotated[
        RiotRegionalRoute,
        Query(description="Riot regional route for Match-V5 match data."),
    ] = RiotRegionalRoute.SEA,
    queue: Annotated[str, Query(min_length=1, description="Ranked queue.")] = "RANKED_SOLO_5x5",
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
    await job_queue.enqueue(job.job_id)
    return _status_response(job)


@router.get(
    "/status",
    response_model=JobStatusListResponse,
    summary="List job status",
)
async def list_job_status(
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
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
) -> JobStatusListResponse:
    statuses = {JobStatus.QUEUED, JobStatus.RUNNING} if running_only else None
    jobs = await store.list_jobs(statuses=statuses)
    generated_at = datetime.now(UTC)
    return JobStatusListResponse(
        generated_at=generated_at,
        running_only=running_only,
        verbose=verbose,
        include_events=include_events,
        include_result=include_result,
        jobs=[
            _job_payload(
                job,
                now=generated_at,
                verbose=verbose,
                include_events=include_events,
                include_result=include_result,
            )
            for job in jobs
        ],
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
)
async def get_job(
    job_id: str,
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
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
    store: Annotated[InMemoryJobStore, Depends(get_job_store)],
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
                "progress": job.progress.model_dump(mode="json"),
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

    return _success_result(job).model_dump(mode="json")


def _status_response(job: JobRecord, *, include_events: bool = True) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        details=_job_details(job),
        progress=job.progress,
        estimate=_estimate_job(job),
        current_wait=job.current_wait,
        events=job.events if include_events else [],
    )


def _success_result(job: JobRecord) -> JobSucceededResultResponse:
    if job.result is None:
        msg = "Completed job is missing a result."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)
    return JobSucceededResultResponse(
        job_id=job.job_id,
        status=JobStatus.SUCCEEDED,
        details=_job_details(job),
        summary=job.result.summary,
        estimate=_estimate_job(job),
        player_puuids=job.result.player_puuids,
        match_ids=job.result.match_ids,
        matches=job.result.matches,
        errors=job.result.errors,
    )


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
        "progress": job.progress.model_dump(mode="json"),
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


def _job_details(job: JobRecord) -> JobDetails:
    return JobDetails(
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


def _job_source(job: JobRecord) -> str:
    if job.job_type is JobType.LADDER_INGESTION:
        return "league_v4_apex_ladder"
    return "unknown"


def _queue_label(queue: str) -> str:
    queue_labels = {
        "RANKED_SOLO_5x5": "Ranked Solo/Duo",
        "RANKED_FLEX_SR": "Ranked Flex",
    }
    return queue_labels.get(queue, queue)


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
        return "queued", "Waiting for the job worker to start ladder ingestion."
    if job.status is JobStatus.FAILED:
        stage = job.error.stage if job.error is not None and job.error.stage else "failed"
        return stage, "Job failed before completion."
    if job.status is JobStatus.SUCCEEDED:
        return "completed", "Job completed successfully."
    if job.current_wait is not None:
        return job.current_wait.stage or "rate_limit_wait", job.current_wait.message

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
