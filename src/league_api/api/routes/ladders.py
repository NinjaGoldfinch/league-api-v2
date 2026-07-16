from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from league_api.jobs.models import LadderPlayersParams, RankedDivision, RankedTier
from league_api.ladders.store import LadderPlayer, LadderPlayerStore
from league_api.matches.references import MatchReferenceStore
from league_api.matches.store import MatchStore
from league_api.riot.cache import RiotCacheStore, build_riot_cache_key
from league_api.riot.queues import LeagueQueue
from league_api.riot.routing import RiotPlatformRoute, get_platform_base_url

router = APIRouter(prefix="/ladders", tags=["ladders"])


class LadderPlayerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    puuid: str
    game_name: str | None
    tag_line: str | None
    riot_id: str | None
    identity_status: str
    profile_icon_id: int | None
    icon_status: str
    league_points: int
    wins: int
    losses: int
    rank: str | None
    hot_streak: bool
    veteran: bool
    inactive: bool
    fresh_blood: bool
    fetched_at: datetime


class LadderPlayersPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    offset: int
    limit: int
    players: list[LadderPlayerResponse]


class LadderMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: str
    player_count: int
    is_duplicate: bool
    detail_status: str
    game_creation: int | None = None
    game_duration: int | None = None
    game_mode: str | None = None
    queue_id: int | None = None


class LadderMatchesPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    offset: int
    limit: int
    matches: list[LadderMatchResponse]


class StoredMatchDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: str
    regional_route: str
    payload: dict[str, Any]


def get_ladder_player_store(request: Request) -> LadderPlayerStore:
    return cast(LadderPlayerStore, request.app.state.ladder_player_store)


def get_match_reference_store(request: Request) -> MatchReferenceStore:
    return cast(MatchReferenceStore, request.app.state.match_reference_store)


def get_match_store(request: Request) -> MatchStore:
    return cast(MatchStore, request.app.state.match_store)


@router.get("/players", response_model=LadderPlayersPage)
async def list_ladder_players(
    request: Request,
    store: Annotated[LadderPlayerStore, Depends(get_ladder_player_store)],
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1,
    queue: LeagueQueue = LeagueQueue.RANKED_SOLO_5X5,
    tier: RankedTier = RankedTier.CHALLENGER,
    division: RankedDivision | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    search: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> LadderPlayersPage:
    try:
        LadderPlayersParams(
            platform_route=platform_route,
            queue=queue,
            tier=tier,
            division=division,
            page=page,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    players, total = await store.list_players(
        platform_route=platform_route.value,
        queue=queue.value,
        tier=tier.value,
        division=division.value if division else None,
        page=page,
        search=search,
        offset=offset,
        limit=limit,
    )
    cache_store = cast(RiotCacheStore | None, getattr(request.app.state, "riot_cache_store", None))
    return LadderPlayersPage(
        total=total,
        offset=offset,
        limit=limit,
        players=[await _response(player, cache_store) for player in players],
    )


async def _response(
    player: LadderPlayer, cache_store: RiotCacheStore | None
) -> LadderPlayerResponse:
    icon_id = await _cached_icon_id(player, cache_store)
    riot_id = (
        f"{player.game_name}#{player.tag_line}" if player.game_name and player.tag_line else None
    )
    return LadderPlayerResponse(
        puuid=player.puuid,
        game_name=player.game_name,
        tag_line=player.tag_line,
        riot_id=riot_id,
        identity_status="resolved" if riot_id else "unresolved",
        profile_icon_id=icon_id,
        icon_status="cached" if icon_id is not None else "unavailable",
        league_points=player.league_points,
        wins=player.wins,
        losses=player.losses,
        rank=player.rank,
        hot_streak=player.hot_streak,
        veteran=player.veteran,
        inactive=player.inactive,
        fresh_blood=player.fresh_blood,
        fetched_at=player.fetched_at,
    )


async def _cached_icon_id(player: LadderPlayer, cache_store: RiotCacheStore | None) -> int | None:
    if cache_store is None:
        return None
    path = f"/lol/summoner/v4/summoners/by-puuid/{player.puuid}"
    key = build_riot_cache_key(
        method="GET", base_url=get_platform_base_url(player.platform_route), path=path, params=None
    )
    entry = await cache_store.get(key.cache_key)
    payload: Any = entry.payload if entry is not None else None
    value = payload.get("profileIconId") if isinstance(payload, dict) else None
    return value if isinstance(value, int) else None


@router.get("/matches", response_model=LadderMatchesPage)
async def list_ladder_matches(
    player_store: Annotated[LadderPlayerStore, Depends(get_ladder_player_store)],
    reference_store: Annotated[MatchReferenceStore, Depends(get_match_reference_store)],
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    platform_route: RiotPlatformRoute = RiotPlatformRoute.OC1,
    queue: LeagueQueue = LeagueQueue.RANKED_SOLO_5X5,
    tier: RankedTier = RankedTier.CHALLENGER,
    division: RankedDivision | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    search: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> LadderMatchesPage:
    _validate_target(platform_route, queue, tier, division, page)
    player_puuids = await player_store.list_puuids(
        platform_route=platform_route.value,
        queue=queue.value,
        tier=tier.value,
        division=division.value if division else None,
        page=page,
    )
    selected_counts, total = await reference_store.list_counts_for_players(
        player_puuids, search=search, offset=offset, limit=limit
    )
    selected_ids = [match_id for match_id, _ in selected_counts]
    counts = dict(selected_counts)
    payloads = await match_store.get_matches(selected_ids)
    return LadderMatchesPage(
        total=total,
        offset=offset,
        limit=limit,
        matches=[
            _match_summary(match_id, counts[match_id], payloads.get(match_id))
            for match_id in selected_ids
        ],
    )


@router.get("/matches/{match_id}", response_model=StoredMatchDetailResponse)
async def get_stored_ladder_match(
    match_id: str,
    match_store: Annotated[MatchStore, Depends(get_match_store)],
) -> StoredMatchDetailResponse:
    record = await match_store.get_match_record(match_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored match not found.")
    return StoredMatchDetailResponse(
        match_id=record.match_id,
        regional_route=record.regional_route,
        payload=record.payload,
    )


def _validate_target(
    platform_route: RiotPlatformRoute,
    queue: LeagueQueue,
    tier: RankedTier,
    division: RankedDivision | None,
    page: int | None,
) -> None:
    try:
        LadderPlayersParams(
            platform_route=platform_route,
            queue=queue,
            tier=tier,
            division=division,
            page=page,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _match_summary(
    match_id: str, player_count: int, payload: dict[str, Any] | None
) -> LadderMatchResponse:
    info = payload.get("info") if isinstance(payload, dict) else None
    return LadderMatchResponse(
        match_id=match_id,
        player_count=player_count,
        is_duplicate=player_count > 1,
        detail_status="stored" if payload is not None else "missing",
        game_creation=_int_field(info, "gameCreation"),
        game_duration=_int_field(info, "gameDuration"),
        game_mode=_str_field(info, "gameMode"),
        queue_id=_int_field(info, "queueId"),
    )


def _int_field(payload: Any, field: str) -> int | None:
    value = payload.get(field) if isinstance(payload, dict) else None
    return value if isinstance(value, int) else None


def _str_field(payload: Any, field: str) -> str | None:
    value = payload.get(field) if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None
