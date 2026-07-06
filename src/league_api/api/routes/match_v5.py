from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query

from league_api.api.routes.riot import RiotClientDependency, call_riot
from league_api.riot.client import RiotClient
from league_api.riot.routing import RiotRegionalRoute

router = APIRouter(prefix="/lol/match/v5", tags=["match-v5"])

RegionalRoute = Annotated[
    RiotRegionalRoute,
    Query(
        description=(
            "Riot regional routing value used as the upstream host prefix. Match-V5 "
            "supports AMERICAS, ASIA, EUROPE, and SEA."
        ),
    ),
]


@router.get(
    "/matches/by-puuid/{puuid}/ids",
    summary="Get a list of match ids by PUUID",
    description=(
        "Mirrors Riot Match-V5 `GET /lol/match/v5/matches/by-puuid/{puuid}/ids`. "
        "`startTime`, `endTime`, `queue`, `type`, `start`, and `count` are passed "
        "through to Riot when provided. `count` follows Riot's documented 0-100 range."
    ),
)
async def get_match_ids_by_puuid(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    puuid: Annotated[str, Path(min_length=1, description="Player PUUID.")],
    regional_route: RegionalRoute = RiotRegionalRoute.SEA,
    start_time: Annotated[
        int | None,
        Query(alias="startTime", ge=0, description="Epoch timestamp in seconds."),
    ] = None,
    end_time: Annotated[
        int | None,
        Query(alias="endTime", ge=0, description="Epoch timestamp in seconds."),
    ] = None,
    queue: Annotated[int | None, Query(description="Queue ID filter.")] = None,
    match_type: Annotated[
        Literal["ranked", "normal", "tourney", "tutorial"] | None,
        Query(alias="type", description="Match type filter."),
    ] = None,
    start: Annotated[int, Query(ge=0, description="Start index.")] = 0,
    count: Annotated[int, Query(ge=0, le=100, description="Number of match IDs to return.")] = 20,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_match_v5(
                f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
                regional_route=regional_route.value,
                params={
                    "startTime": start_time,
                    "endTime": end_time,
                    "queue": queue,
                    "type": match_type,
                    "start": start,
                    "count": count,
                },
            )

    return await call_riot(operation)


@router.get(
    "/matches/by-puuid/{puuid}/replays",
    summary="Get player replays",
    description="Mirrors Riot Match-V5 `GET /lol/match/v5/matches/by-puuid/{puuid}/replays`.",
)
async def get_replays_by_puuid(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    puuid: Annotated[str, Path(min_length=1, description="Player PUUID.")],
    regional_route: RegionalRoute = RiotRegionalRoute.SEA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_match_v5(
                f"/lol/match/v5/matches/by-puuid/{puuid}/replays",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/matches/{matchId}",
    summary="Get a match by match id",
    description="Mirrors Riot Match-V5 `GET /lol/match/v5/matches/{matchId}`.",
)
async def get_match(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    match_id: Annotated[str, Path(alias="matchId", min_length=1, description="Match ID.")],
    regional_route: RegionalRoute = RiotRegionalRoute.SEA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)


@router.get(
    "/matches/{matchId}/timeline",
    summary="Get a match timeline by match id",
    description="Mirrors Riot Match-V5 `GET /lol/match/v5/matches/{matchId}/timeline`.",
)
async def get_timeline(
    riot_client: Annotated[RiotClient, RiotClientDependency],
    match_id: Annotated[str, Path(alias="matchId", min_length=1, description="Match ID.")],
    regional_route: RegionalRoute = RiotRegionalRoute.SEA,
) -> Any:
    async def operation() -> Any:
        async with riot_client:
            return await riot_client.get_match_v5(
                f"/lol/match/v5/matches/{match_id}/timeline",
                regional_route=regional_route.value,
            )

    return await call_riot(operation)
