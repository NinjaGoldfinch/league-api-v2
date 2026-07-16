from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from league_api.riot.queues import LeagueQueue
from league_api.riot.routing import RiotAccountRegionalRoute, RiotPlatformRoute, RiotRegionalRoute


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobType(StrEnum):
    LADDER_INGESTION = "ladder_ingestion"
    LADDER_PLAYERS = "ladder_players"
    PROFILE_FETCH = "profile_fetch"


class LadderType(StrEnum):
    CHALLENGER = "challenger"


class RankedTier(StrEnum):
    CHALLENGER = "CHALLENGER"
    GRANDMASTER = "GRANDMASTER"
    MASTER = "MASTER"
    DIAMOND = "DIAMOND"
    EMERALD = "EMERALD"
    PLATINUM = "PLATINUM"
    GOLD = "GOLD"
    SILVER = "SILVER"
    BRONZE = "BRONZE"
    IRON = "IRON"


class RankedDivision(StrEnum):
    DIVISION_I = "I"
    DIVISION_II = "II"
    DIVISION_III = "III"
    DIVISION_IV = "IV"


class LadderFetchMode(StrEnum):
    LADDER_ONLY = "ladder_only"
    LADDER_AND_MATCHES = "ladder_and_matches"


class JobProgress(StrictBaseModel):
    players_discovered: int = 0
    players_processed: int = 0
    match_id_pages_fetched: int = 0
    match_id_pages_with_results: int = 0
    match_ids_discovered: int = 0
    unique_match_ids: int = 0
    duplicate_match_ids_skipped: int = 0
    matches_fetched: int = 0
    errors: int = 0
    identities_resolved: int = 0
    identities_reused: int = 0
    identities_unresolved: int = 0
    current_player_puuid: str | None = None
    phase: str | None = None
    current_match_id_start: int | None = None
    match_id_pages_attempted: int = 0
    match_id_pages_failed: int = 0
    match_id_pages_retried: int = 0
    duplicate_match_references: int = 0
    match_details_reused: int = 0


class LadderJobDetails(StrictBaseModel):
    source: str
    platform_route: RiotPlatformRoute
    regional_route: RiotRegionalRoute
    queue: LeagueQueue
    queue_label: str
    ladder: LadderType
    tier: str
    division: str | None = None
    match_count_per_player: int
    player_count: int
    match_id_request_count: int
    match_detail_request_count: int


class ProfileJobDetails(StrictBaseModel):
    source: str
    riot_id: str
    game_name: str
    tag_line: str
    puuid: str | None = None
    account_regional_route: RiotAccountRegionalRoute
    platform_route: RiotPlatformRoute
    regional_route: RiotRegionalRoute
    match_count: int | None
    match_id_request_count: int
    match_id_page_request_count: int
    match_id_pages_with_results: int
    match_detail_request_count: int


class LadderPlayersJobDetails(StrictBaseModel):
    source: str
    platform_route: RiotPlatformRoute
    regional_route: RiotRegionalRoute
    queue: LeagueQueue
    queue_label: str
    tier: RankedTier
    division: RankedDivision | None
    page: int | None
    player_count: int
    identities_resolved: int
    identities_reused: int
    identities_unresolved: int
    mode: LadderFetchMode


JobDetails = LadderJobDetails | LadderPlayersJobDetails | ProfileJobDetails


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
    queue: LeagueQueue = LeagueQueue.RANKED_SOLO_5X5
    ladder: LadderType = LadderType.CHALLENGER
    match_count: int = Field(default=20, ge=1, le=100)


class ProfileFetchParams(StrictBaseModel):
    game_name: str = Field(min_length=1)
    tag_line: str = Field(min_length=1)
    account_regional_route: RiotAccountRegionalRoute = RiotAccountRegionalRoute.ASIA
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA
    match_count: int | None = Field(default=None, ge=1, le=1000)
    account: dict[str, Any] | None = None
    summoner: dict[str, Any] | None = None
    match_ids: list[str] | None = None

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


class LadderPlayersParams(StrictBaseModel):
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1
    account_regional_route: RiotAccountRegionalRoute = RiotAccountRegionalRoute.ASIA
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA
    queue: LeagueQueue = LeagueQueue.RANKED_SOLO_5X5
    tier: RankedTier = RankedTier.CHALLENGER
    division: RankedDivision | None = None
    page: int | None = Field(default=None, ge=1)
    mode: LadderFetchMode = LadderFetchMode.LADDER_ONLY

    def model_post_init(self, __context: Any) -> None:
        apex = self.tier in {RankedTier.CHALLENGER, RankedTier.GRANDMASTER, RankedTier.MASTER}
        if apex and (self.division is not None or self.page is not None):
            raise ValueError("Apex ladders do not accept division or page.")
        if not apex and (self.division is None or self.page is None):
            raise ValueError("Lower-tier ladders require division and page.")


class LadderIngestionResult(StrictBaseModel):
    summary: JobProgress
    player_puuids: list[str]
    match_ids: list[str]
    matches: dict[str, dict[str, Any]]
    errors: list[JobError] = Field(default_factory=list)


class ProfileFetchResult(StrictBaseModel):
    summary: JobProgress
    account: dict[str, Any]
    summoner: dict[str, Any]
    match_ids: list[str]
    matches: dict[str, dict[str, Any]]
    errors: list[JobError] = Field(default_factory=list)


class LadderPlayersResult(StrictBaseModel):
    summary: JobProgress
    player_puuids: list[str]
    match_ids: list[str] = Field(default_factory=list)
    errors: list[JobError] = Field(default_factory=list)


JobParams = LadderIngestionParams | LadderPlayersParams | ProfileFetchParams
JobResult = LadderIngestionResult | LadderPlayersResult | ProfileFetchResult


class JobRecord(StrictBaseModel):
    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    params: JobParams
    result: JobResult | None = None
    error: JobError | None = None
    current_wait: JobWait | None = None
    events: list[JobEvent] = Field(default_factory=list)
