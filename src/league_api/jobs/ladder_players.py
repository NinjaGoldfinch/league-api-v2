from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Protocol, cast
from urllib.parse import quote

from league_api.jobs.ingestion import _job_riot_request_event_handler
from league_api.jobs.models import (
    JobError,
    JobProgress,
    LadderPlayersParams,
    LadderPlayersResult,
    RankedTier,
)
from league_api.jobs.store import JobStore
from league_api.ladders.store import LadderPlayer, LadderPlayerStore
from league_api.matches.references import MatchReferenceStore
from league_api.matches.store import MatchStore
from league_api.players.store import PlayerIdentity, PlayerIdentityStore, hydrate_identities
from league_api.riot.client import RiotClient, RiotRequestEventHandler
from league_api.riot.rate_limiter import RiotRateLimitAudience

MATCH_IDENTITY_MAX_AGE = timedelta(hours=24)


class LadderPlayersRiotClient(Protocol):
    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...
    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...
    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any: ...


class LadderPlayersClientContext(Protocol):
    async def __aenter__(self) -> LadderPlayersRiotClient: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class LadderPlayersClientFactory(Protocol):
    def __call__(
        self, *, request_event_handler: RiotRequestEventHandler | None = None
    ) -> LadderPlayersClientContext: ...


def _default_factory(
    *, request_event_handler: RiotRequestEventHandler | None = None
) -> LadderPlayersClientContext:
    return RiotClient.from_settings(request_event_handler=request_event_handler)


async def run_ladder_players_fetch(
    params: LadderPlayersParams,
    store: JobStore,
    job_id: str,
    *,
    player_store: LadderPlayerStore,
    identity_store: PlayerIdentityStore | None = None,
    match_store: MatchStore | None = None,
    match_reference_store: MatchReferenceStore | None = None,
    riot_client_factory: LadderPlayersClientFactory = _default_factory,
) -> LadderPlayersResult:
    progress = JobProgress()
    errors: list[JobError] = []
    fetched_at = datetime.now(UTC)
    async with riot_client_factory(
        request_event_handler=_job_riot_request_event_handler(store, job_id)
    ) as client:
        path, query = _ladder_request(params)
        payload = await client.get_league_v4(
            path,
            platform_route=params.platform_route.value,
            params=query,
            rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
        )
        entries = _entries(payload, apex=params.tier in _APEX_TIERS)
        progress.players_discovered = len(entries)
        await store.update_progress(job_id, progress)
        players: list[LadderPlayer] = []
        for entry in entries:
            puuid = entry.get("puuid")
            if not isinstance(puuid, str) or not puuid:
                continue
            progress.current_player_puuid = puuid
            stored_identity = None
            if identity_store is not None:
                stored_identity = await identity_store.get_by_puuid(
                    puuid, max_age=MATCH_IDENTITY_MAX_AGE
                )
            identity = (
                (stored_identity.game_name, stored_identity.tag_line)
                if stored_identity is not None
                else await player_store.get_identity(puuid)
                if identity_store is None
                else None
            )
            if identity is not None:
                progress.identities_reused += 1
            else:
                try:
                    account = await client.get_account_v1(
                        f"/riot/account/v1/accounts/by-puuid/{quote(puuid, safe='')}",
                        regional_route=params.account_regional_route.value,
                        rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
                    )
                    identity = _identity(account)
                    if identity_store is not None:
                        await identity_store.upsert(
                            PlayerIdentity(
                                puuid=puuid,
                                game_name=identity[0],
                                tag_line=identity[1],
                                observed_at=datetime.now(UTC),
                            )
                        )
                    progress.identities_resolved += 1
                except Exception as exc:
                    errors.append(
                        JobError(
                            message=str(exc),
                            stage="account",
                            error_type=exc.__class__.__name__,
                            player_puuid=puuid,
                        )
                    )
                    progress.identities_unresolved += 1
            players.append(_player(params, entry, identity, fetched_at))
            progress.players_processed += 1
            progress.errors = len(errors)
            await store.update_progress(job_id, progress)
        progress.current_player_puuid = None
        await _persist_players(player_store, params, players)
        match_ids: list[str] = []
        if params.mode.value == "ladder_and_matches":
            if match_store is None or match_reference_store is None:
                raise RuntimeError("Combined ladder fetching requires durable match stores.")
            match_ids = await _discover_and_fetch_matches(
                client=client,
                params=params,
                players=players,
                progress=progress,
                errors=errors,
                job_store=store,
                job_id=job_id,
                match_store=match_store,
                reference_store=match_reference_store,
                identity_store=identity_store,
            )
    return LadderPlayersResult(
        summary=progress,
        player_puuids=[p.puuid for p in players],
        match_ids=match_ids,
        errors=errors,
    )


async def _persist_players(
    player_store: LadderPlayerStore,
    params: LadderPlayersParams,
    players: list[LadderPlayer],
) -> None:
    await player_store.replace_target(
        platform_route=params.platform_route.value,
        queue=params.queue.value,
        tier=params.tier.value,
        division=params.division.value if params.division else None,
        page=params.page,
        players=players,
    )


MATCH_ID_PAGE_SIZE = 100
DEFERRED_RETRY_PASSES = 3


async def _discover_and_fetch_matches(
    *,
    client: LadderPlayersRiotClient,
    params: LadderPlayersParams,
    players: list[LadderPlayer],
    progress: JobProgress,
    errors: list[JobError],
    job_store: JobStore,
    job_id: str,
    match_store: MatchStore,
    reference_store: MatchReferenceStore,
    identity_store: PlayerIdentityStore | None,
) -> list[str]:
    progress.phase = "match_id_discovery"
    player_ids: dict[str, list[str]] = {player.puuid: [] for player in players}
    failed_pages: list[tuple[str, int, Exception]] = []
    for player in players:
        progress.current_player_puuid = player.puuid
        failure = await _scan_match_id_pages(
            client, params, player.puuid, 0, player_ids[player.puuid], progress, job_store, job_id
        )
        if failure is not None:
            failed_pages.append((player.puuid, failure[0], failure[1]))

    for _retry_pass in range(DEFERRED_RETRY_PASSES):
        if not failed_pages:
            break
        retrying, failed_pages = failed_pages, []
        for puuid, start, _previous_error in retrying:
            progress.current_player_puuid = puuid
            progress.match_id_pages_retried += 1
            failure = await _scan_match_id_pages(
                client, params, puuid, start, player_ids[puuid], progress, job_store, job_id
            )
            if failure is not None:
                failed_pages.append((puuid, failure[0], failure[1]))

    for puuid, start, exc in failed_pages:
        errors.append(
            JobError(
                message=f"Match-ID page start={start} failed after deferred retries: {exc}",
                stage="match_ids",
                error_type=exc.__class__.__name__,
                player_puuid=puuid,
            )
        )

    progress.phase = "match_id_persistence"
    unique_ids: list[str] = []
    seen: set[str] = set()
    total_references = 0
    for puuid, discovered in player_ids.items():
        deduped_player_ids = list(dict.fromkeys(discovered))
        total_references += len(deduped_player_ids)
        await reference_store.upsert(puuid, deduped_player_ids)
        for match_id in deduped_player_ids:
            if match_id not in seen:
                seen.add(match_id)
                unique_ids.append(match_id)
    progress.match_ids_discovered = total_references
    progress.unique_match_ids = len(unique_ids)
    progress.duplicate_match_references = total_references - len(unique_ids)
    progress.errors = len(errors)
    await job_store.update_progress(job_id, progress)

    progress.phase = "match_details"
    progress.current_player_puuid = None
    stored = await match_store.get_matches(unique_ids)
    progress.match_details_reused = len(stored)
    available_ids = set(stored)
    for match_id in unique_ids:
        if match_id in stored:
            progress.matches_fetched += 1
            continue
        try:
            payload = await client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}",
                regional_route=params.regional_route.value,
                rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
            )
            if not isinstance(payload, dict):
                raise ValueError("Match detail response did not return an object.")
            await match_store.save_match(
                match_id, regional_route=params.regional_route.value, payload=payload
            )
            if identity_store is not None:
                await hydrate_identities(identity_store, payload)
            available_ids.add(match_id)
            progress.matches_fetched += 1
        except Exception as exc:
            errors.append(
                JobError(
                    message=str(exc),
                    stage="match_detail",
                    error_type=exc.__class__.__name__,
                    match_id=match_id,
                )
            )
        progress.errors = len(errors)
        await job_store.update_progress(job_id, progress)

    for puuid, discovered in player_ids.items():
        await match_store.link_player_matches(
            puuid, [match_id for match_id in dict.fromkeys(discovered) if match_id in available_ids]
        )
    progress.phase = "completed"
    await job_store.update_progress(job_id, progress)
    return unique_ids


async def _scan_match_id_pages(
    client: LadderPlayersRiotClient,
    params: LadderPlayersParams,
    puuid: str,
    start: int,
    output: list[str],
    progress: JobProgress,
    job_store: JobStore,
    job_id: str,
) -> tuple[int, Exception] | None:
    while True:
        progress.current_match_id_start = start
        progress.match_id_pages_attempted += 1
        try:
            payload = await client.get_match_v5(
                f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
                regional_route=params.regional_route.value,
                params={"start": start, "count": MATCH_ID_PAGE_SIZE},
                rate_limit_audience=RiotRateLimitAudience.AUTOMATIC,
            )
            if not isinstance(payload, list):
                raise ValueError("Match ID response did not return a list.")
            page_ids = [item for item in payload if isinstance(item, str) and item]
            output.extend(page_ids)
            progress.match_id_pages_fetched += 1
            if page_ids:
                progress.match_id_pages_with_results += 1
            await job_store.update_progress(job_id, progress)
            if len(page_ids) < MATCH_ID_PAGE_SIZE:
                return None
            start += MATCH_ID_PAGE_SIZE
        except Exception as exc:
            progress.match_id_pages_failed += 1
            await job_store.update_progress(job_id, progress)
            return start, exc


_APEX_TIERS = {RankedTier.CHALLENGER, RankedTier.GRANDMASTER, RankedTier.MASTER}


def _ladder_request(
    params: LadderPlayersParams,
) -> tuple[str, dict[str, int | str | None] | None]:
    if params.tier in _APEX_TIERS:
        prefix = params.tier.value.lower()
        return f"/lol/league/v4/{prefix}leagues/by-queue/{params.queue.value}", None
    division = params.division
    if division is None:
        raise ValueError("Lower-tier ladders require a division.")
    return (
        f"/lol/league/v4/entries/{params.queue.value}/{params.tier.value}/{division.value}",
        {"page": cast(int, params.page)},
    )


def _entries(payload: Any, *, apex: bool) -> list[dict[str, Any]]:
    raw = payload.get("entries") if apex and isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ValueError("Ladder response entries did not return a list.")
    return [cast(dict[str, Any], item) for item in raw if isinstance(item, dict)]


def _identity(payload: Any) -> tuple[str, str]:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("gameName"), str)
        or not isinstance(payload.get("tagLine"), str)
    ):
        raise ValueError("Account-V1 response did not contain a Riot ID.")
    return payload["gameName"], payload["tagLine"]


def _player(
    params: LadderPlayersParams,
    entry: dict[str, Any],
    identity: tuple[str, str] | None,
    fetched_at: datetime,
) -> LadderPlayer:
    return LadderPlayer(
        platform_route=params.platform_route.value,
        queue=params.queue.value,
        tier=params.tier.value,
        division=params.division.value if params.division else None,
        page=params.page,
        puuid=cast(str, entry["puuid"]),
        league_points=int(entry.get("leaguePoints") or 0),
        wins=int(entry.get("wins") or 0),
        losses=int(entry.get("losses") or 0),
        rank=entry.get("rank") if isinstance(entry.get("rank"), str) else None,
        hot_streak=bool(entry.get("hotStreak")),
        veteran=bool(entry.get("veteran")),
        inactive=bool(entry.get("inactive")),
        fresh_blood=bool(entry.get("freshBlood")),
        game_name=identity[0] if identity else None,
        tag_line=identity[1] if identity else None,
        fetched_at=fetched_at,
    )
