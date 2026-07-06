from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, cast

from league_api.jobs.models import (
    JobError,
    JobProgress,
    LadderIngestionParams,
    LadderIngestionResult,
    LadderType,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.client import RiotClient
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


RiotClientFactory = Callable[[], RiotClientContext]


def _default_riot_client_factory() -> RiotClientContext:
    return RiotClient.from_settings()


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

    async with riot_client_factory() as riot_client:
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
