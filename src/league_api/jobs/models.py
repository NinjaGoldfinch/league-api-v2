from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from league_api.riot.routing import RiotPlatformRoute, RiotRegionalRoute


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobType(StrEnum):
    LADDER_INGESTION = "ladder_ingestion"


class LadderType(StrEnum):
    CHALLENGER = "challenger"


class JobProgress(StrictBaseModel):
    players_discovered: int = 0
    players_processed: int = 0
    match_ids_discovered: int = 0
    unique_match_ids: int = 0
    duplicate_match_ids_skipped: int = 0
    matches_fetched: int = 0
    errors: int = 0


class JobDetails(StrictBaseModel):
    source: str
    platform_route: RiotPlatformRoute
    regional_route: RiotRegionalRoute
    queue: str
    queue_label: str
    ladder: LadderType
    tier: str
    division: str | None = None
    match_count_per_player: int
    player_count: int
    match_id_request_count: int
    match_detail_request_count: int


class JobEstimate(StrictBaseModel):
    stage: str
    description: str
    current_path: str | None = None
    requests_completed: int
    requests_total: int | None = None
    requests_remaining: int | None = None
    percent_complete: float | None = None
    average_seconds_per_request: float | None = None
    rate_limit_seconds_remaining: float | None = None
    rate_limit_label: str | None = None
    estimated_seconds_remaining: float | None = None
    estimated_completed_at: datetime | None = None


class JobError(StrictBaseModel):
    message: str
    stage: str | None = None
    error_type: str | None = None
    player_puuid: str | None = None
    match_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobWait(StrictBaseModel):
    reason: str
    message: str
    resume_at: datetime
    wait_seconds: float
    stage: str | None = None
    path: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobEvent(StrictBaseModel):
    event_type: str
    message: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: str | None = None
    path: str | None = None
    status_code: int | None = None
    attempt: int | None = None
    wait_seconds: float | None = None
    resume_at: datetime | None = None
    retry_after: str | None = None


class LadderIngestionParams(StrictBaseModel):
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA
    queue: str = "RANKED_SOLO_5x5"
    ladder: LadderType = LadderType.CHALLENGER
    match_count: int = Field(default=20, ge=1, le=100)


class LadderIngestionResult(StrictBaseModel):
    summary: JobProgress
    player_puuids: list[str]
    match_ids: list[str]
    matches: dict[str, dict[str, Any]]
    errors: list[JobError] = Field(default_factory=list)


class JobRecord(StrictBaseModel):
    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    params: LadderIngestionParams
    result: LadderIngestionResult | None = None
    error: JobError | None = None
    current_wait: JobWait | None = None
    events: list[JobEvent] = Field(default_factory=list)
