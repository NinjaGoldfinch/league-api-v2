from types import TracebackType
from typing import Any, Protocol, cast

from league_api.jobs.models import (
    JobError,
    JobEvent,
    JobProgress,
    JobWait,
    LadderIngestionParams,
    LadderIngestionResult,
    LadderType,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.client import RiotClient, RiotRequestEvent, RiotRequestEventHandler
from league_api.riot.routing import RiotPlatformRoute, RiotRegionalRoute


class RiotApiClient(Protocol):
    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str | RiotRegionalRoute = "sea",
        params: dict[str, int | str | None] | None = None,
    ) -> Any: ...

    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str | RiotPlatformRoute = "oc1",
        params: dict[str, int | str | None] | None = None,
    ) -> Any: ...


class RiotClientContext(Protocol):
    async def __aenter__(self) -> RiotApiClient: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class RiotClientFactory(Protocol):
    def __call__(
        self,
        *,
        request_event_handler: RiotRequestEventHandler | None = None,
    ) -> RiotClientContext: ...


def _default_riot_client_factory(
    *,
    request_event_handler: RiotRequestEventHandler | None = None,
) -> RiotClientContext:
    return RiotClient.from_settings(request_event_handler=request_event_handler)


async def run_ladder_ingestion(
    params: LadderIngestionParams,
    store: InMemoryJobStore,
    job_id: str,
    *,
    riot_client_factory: RiotClientFactory = _default_riot_client_factory,
) -> LadderIngestionResult:
    progress = JobProgress()
    errors: list[JobError] = []
    player_puuids: list[str] = []
    match_ids: list[str] = []
    seen_match_ids: set[str] = set()
    matches: dict[str, dict[str, Any]] = {}

    async with riot_client_factory(
        request_event_handler=_job_riot_request_event_handler(store, job_id)
    ) as riot_client:
        if params.ladder == LadderType.CHALLENGER:
            ladder_payload = await riot_client.get_league_v4(
                f"/lol/league/v4/challengerleagues/by-queue/{params.queue}",
                platform_route=params.platform_route,
            )
        else:
            msg = f"Unsupported ladder type: {params.ladder}"
            raise ValueError(msg)

        player_puuids = _extract_player_puuids(ladder_payload)
        progress.players_discovered = len(player_puuids)
        await store.update_progress(job_id, progress)

        for puuid in player_puuids:
            player_match_ids = await _fetch_player_match_ids(riot_client, params, puuid, errors)
            if player_match_ids is None:
                progress.errors = len(errors)
                progress.players_processed += 1
                await store.update_progress(job_id, progress)
                continue

            progress.match_ids_discovered += len(player_match_ids)
            for match_id in player_match_ids:
                if match_id in seen_match_ids:
                    progress.duplicate_match_ids_skipped += 1
                    continue
                seen_match_ids.add(match_id)
                match_ids.append(match_id)

            progress.players_processed += 1
            progress.unique_match_ids = len(match_ids)
            await store.update_progress(job_id, progress)

        for match_id in match_ids:
            match_payload = await riot_client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}",
                regional_route=params.regional_route,
            )
            if not isinstance(match_payload, dict):
                msg = f"Match detail for {match_id} did not return an object."
                raise ValueError(msg)
            matches[match_id] = cast(dict[str, Any], match_payload)
            progress.matches_fetched += 1
            await store.update_progress(job_id, progress)

    return LadderIngestionResult(
        summary=progress,
        player_puuids=player_puuids,
        match_ids=match_ids,
        matches=matches,
        errors=errors,
    )


def _extract_player_puuids(ladder_payload: Any) -> list[str]:
    if not isinstance(ladder_payload, dict):
        msg = "Ladder response did not return an object."
        raise ValueError(msg)

    entries_payload = ladder_payload.get("entries", [])
    if not isinstance(entries_payload, list):
        msg = "Ladder response entries field did not return a list."
        raise ValueError(msg)

    player_puuids: list[str] = []
    for entry in entries_payload:
        if not isinstance(entry, dict):
            continue
        puuid = entry.get("puuid")
        if isinstance(puuid, str) and puuid:
            player_puuids.append(puuid)
    return player_puuids


async def _fetch_player_match_ids(
    riot_client: RiotApiClient,
    params: LadderIngestionParams,
    puuid: str,
    errors: list[JobError],
) -> list[str] | None:
    match_ids_payload = await riot_client.get_match_v5(
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
        regional_route=params.regional_route,
        params={
            "start": 0,
            "count": params.match_count,
        },
    )
    if not isinstance(match_ids_payload, list):
        errors.append(
            JobError(
                message="Match ID response did not return a list.",
                stage="match_ids",
                player_puuid=puuid,
            )
        )
        return None

    return [match_id for match_id in match_ids_payload if isinstance(match_id, str) and match_id]


def _job_riot_request_event_handler(
    store: InMemoryJobStore,
    job_id: str,
) -> RiotRequestEventHandler:
    async def handle_event(event: RiotRequestEvent) -> None:
        stage = _stage_for_riot_path(event.path)
        job_event = JobEvent(
            event_type=event.event_type,
            message=_message_for_riot_event(event),
            stage=stage,
            path=event.path,
            status_code=event.status_code,
            attempt=event.attempt,
            wait_seconds=event.wait_seconds,
            resume_at=event.resume_at,
            retry_after=event.retry_after,
            occurred_at=event.occurred_at,
        )

        if event.event_type == "rate_limit_wait" and event.resume_at is not None:
            wait = JobWait(
                reason=event.rate_limit_reason or "rate_limit",
                message=_message_for_riot_event(event),
                resume_at=event.resume_at,
                wait_seconds=event.wait_seconds or 0.0,
                stage=stage,
                path=event.path,
                occurred_at=event.occurred_at,
            )
            await store.record_event(job_id, job_event, current_wait=wait)
            return

        await store.record_event(
            job_id,
            job_event,
            clear_current_wait=event.event_type in {"request_started", "request_succeeded"},
        )

    return handle_event


def _stage_for_riot_path(path: str) -> str:
    if "/lol/league/v4/" in path:
        return "ladder"
    if path.endswith("/ids") and "/lol/match/v5/matches/by-puuid/" in path:
        return "match_ids"
    if "/lol/match/v5/matches/" in path:
        return "match_detail"
    return "riot_request"


def _message_for_riot_event(event: RiotRequestEvent) -> str:
    if event.event_type == "rate_limit_wait":
        resume_text = event.resume_at.isoformat() if event.resume_at is not None else "unknown"
        reason = "Riot 429" if event.rate_limit_reason == "riot_429" else "Riot rate limit"
        return f"Waiting for {reason}; resumes at {resume_text}."
    if event.event_type == "request_started":
        return f"Riot request started for {event.path}."
    if event.event_type == "request_succeeded":
        return f"Riot request succeeded with status {event.status_code}."
    if event.status_code is not None:
        return f"Riot request failed with status {event.status_code}."
    return event.error or "Riot request failed before receiving a response."
