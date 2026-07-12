from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from league_api.api.routes.riot import RiotClientDependency
from league_api.matches.store import MatchStore, StoredMatch
from league_api.riot.cache import RiotCacheEntry, RiotCacheStore, build_riot_cache_key
from league_api.riot.client import RiotClient, get_last_riot_cache_status
from league_api.riot.routing import RiotRegionalRoute, get_regional_base_url

router = APIRouter(prefix="/manager/api", tags=["experimental-manager"])


class ManagerSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    durable_matches: int
    player_match_links: int
    riot_cache_entries: int
    cache_available: bool


class ManagerMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    regional_route: str
    game_creation: int | None
    fetched_at: str
    linked_puuids: list[str]
    payload: dict[str, Any] | None = None
    cache_status: str | None = None
    cache_fetched_at: str | None = None
    cache_expires_at: str | None = None
    cache_stale_until: str | None = None


class ManagerMatchPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[ManagerMatchResponse]
    offset: int
    limit: int
    total: int


class MatchFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_ids: list[str] = Field(min_length=1, max_length=100)
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA
    puuid: str | None = Field(default=None, min_length=1)
    force_upstream: bool = False


class MatchFetchItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    success: bool
    cache_status: str | None = None
    persisted: bool = False
    linked: bool = False
    error: str | None = None


class MatchFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: int
    succeeded: int
    failed: int
    results: list[MatchFetchItemResponse]


class DeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    cache_deleted: bool = False
    player_link_deleted: bool = False
    durable_match_deleted: bool = False


class PruneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pruned: int


def get_match_store(request: Request) -> MatchStore:
    return cast(MatchStore, request.app.state.match_store)


def get_cache_store(request: Request) -> RiotCacheStore | None:
    return cast(RiotCacheStore | None, getattr(request.app.state, "riot_cache_store", None))


@router.get("/summary", response_model=ManagerSummaryResponse)
async def get_summary(
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    cache_store: Annotated[RiotCacheStore | None, Depends(get_cache_store)],
) -> ManagerSummaryResponse:
    return ManagerSummaryResponse(
        durable_matches=await match_store.count_matches(),
        player_match_links=await match_store.count_player_links(),
        riot_cache_entries=await cache_store.count() if cache_store is not None else 0,
        cache_available=cache_store is not None,
    )


@router.get("/matches", response_model=ManagerMatchPageResponse)
async def list_matches(
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    search: Annotated[str | None, Query(min_length=1)] = None,
    puuid: Annotated[str | None, Query(min_length=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ManagerMatchPageResponse:
    page = await match_store.list_matches(
        search=search.strip() if search is not None else None,
        puuid=puuid,
        offset=offset,
        limit=limit,
    )
    return ManagerMatchPageResponse(
        matches=[_match_response(item) for item in page.matches],
        offset=offset,
        limit=limit,
        total=page.total,
    )


@router.get("/matches/{matchId}", response_model=ManagerMatchResponse)
async def get_match_detail(
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    cache_store: Annotated[RiotCacheStore | None, Depends(get_cache_store)],
    match_id: Annotated[str, Path(alias="matchId", min_length=1)],
) -> ManagerMatchResponse:
    record = await match_store.get_match_record(match_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found.")
    cache_entry = await _match_cache_entry(cache_store, match_id, record.regional_route)
    return _match_response(record, payload=True, cache_entry=cache_entry)


@router.post("/matches/fetch", response_model=MatchFetchResponse)
async def fetch_matches(
    body: MatchFetchRequest,
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    riot_client: Annotated[RiotClient, RiotClientDependency],
) -> MatchFetchResponse:
    unique_match_ids = list(dict.fromkeys(item.strip() for item in body.match_ids if item.strip()))
    if not unique_match_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No match IDs."
        )

    results: list[MatchFetchItemResponse] = []
    async with riot_client:
        for match_id in unique_match_ids:
            try:
                payload = await riot_client.get_match_v5(
                    f"/lol/match/v5/matches/{match_id}",
                    regional_route=body.regional_route,
                    bypass_cache=body.force_upstream,
                )
                if not isinstance(payload, dict):
                    raise ValueError("Riot returned a non-object match payload.")
                payload_match_id = payload.get("metadata", {}).get("matchId")
                if payload_match_id not in (None, match_id):
                    raise ValueError(f"Riot returned match {payload_match_id!r}.")
                await match_store.save_match(
                    match_id,
                    regional_route=body.regional_route.value,
                    payload=payload,
                )
                linked = False
                if body.puuid is not None:
                    await match_store.link_player_matches(body.puuid, [match_id])
                    linked = True
                results.append(
                    MatchFetchItemResponse(
                        match_id=match_id,
                        success=True,
                        cache_status=get_last_riot_cache_status(),
                        persisted=True,
                        linked=linked,
                    )
                )
            except Exception as exc:  # Continue independent debug fetches.
                results.append(
                    MatchFetchItemResponse(
                        match_id=match_id,
                        success=False,
                        cache_status=get_last_riot_cache_status(),
                        error=str(exc),
                    )
                )
    succeeded = sum(item.success for item in results)
    return MatchFetchResponse(
        requested=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


@router.delete("/cache/matches/{matchId}", response_model=DeleteResponse)
async def delete_match_cache(
    cache_store: Annotated[RiotCacheStore | None, Depends(get_cache_store)],
    match_id: Annotated[str, Path(alias="matchId", min_length=1)],
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA,
) -> DeleteResponse:
    deleted = await _delete_match_cache(cache_store, match_id, regional_route.value)
    return DeleteResponse(match_id=match_id, cache_deleted=deleted)


@router.delete("/players/{puuid}/matches/{matchId}", response_model=DeleteResponse)
async def unlink_player_match(
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    puuid: Annotated[str, Path(min_length=1)],
    match_id: Annotated[str, Path(alias="matchId", min_length=1)],
) -> DeleteResponse:
    deleted = await match_store.unlink_player_match(puuid, match_id)
    return DeleteResponse(match_id=match_id, player_link_deleted=deleted)


@router.delete("/matches/{matchId}", response_model=DeleteResponse)
async def delete_match(
    match_store: Annotated[MatchStore, Depends(get_match_store)],
    cache_store: Annotated[RiotCacheStore | None, Depends(get_cache_store)],
    match_id: Annotated[str, Path(alias="matchId", min_length=1)],
    include_cache: bool = False,
    regional_route: RiotRegionalRoute = RiotRegionalRoute.SEA,
) -> DeleteResponse:
    cache_deleted = (
        await _delete_match_cache(cache_store, match_id, regional_route.value)
        if include_cache
        else False
    )
    durable_deleted = await match_store.delete_match(match_id)
    return DeleteResponse(
        match_id=match_id,
        cache_deleted=cache_deleted,
        durable_match_deleted=durable_deleted,
    )


@router.post("/cache/prune-expired", response_model=PruneResponse)
async def prune_expired_cache(
    cache_store: Annotated[RiotCacheStore | None, Depends(get_cache_store)],
) -> PruneResponse:
    return PruneResponse(pruned=await cache_store.prune_expired() if cache_store else 0)


def _match_response(
    record: StoredMatch,
    *,
    payload: bool = False,
    cache_entry: RiotCacheEntry | None = None,
) -> ManagerMatchResponse:
    return ManagerMatchResponse(
        match_id=record.match_id,
        regional_route=record.regional_route,
        game_creation=record.game_creation,
        fetched_at=record.fetched_at.isoformat(),
        linked_puuids=record.linked_puuids,
        payload=record.payload if payload else None,
        cache_status=cache_entry.status_at() if cache_entry else None,
        cache_fetched_at=cache_entry.fetched_at.isoformat() if cache_entry else None,
        cache_expires_at=cache_entry.expires_at.isoformat() if cache_entry else None,
        cache_stale_until=cache_entry.stale_until.isoformat() if cache_entry else None,
    )


def _match_cache_key(match_id: str, regional_route: str) -> str:
    return build_riot_cache_key(
        method="GET",
        base_url=get_regional_base_url(regional_route),
        path=f"/lol/match/v5/matches/{match_id}",
        params=None,
    ).cache_key


async def _match_cache_entry(
    cache_store: RiotCacheStore | None, match_id: str, regional_route: str
) -> RiotCacheEntry | None:
    if cache_store is None:
        return None
    return await cache_store.get(_match_cache_key(match_id, regional_route))


async def _delete_match_cache(
    cache_store: RiotCacheStore | None, match_id: str, regional_route: str
) -> bool:
    if cache_store is None:
        return False
    return await cache_store.delete(_match_cache_key(match_id, regional_route))
