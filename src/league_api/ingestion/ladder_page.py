from typing import Protocol

from pydantic import BaseModel

from league_api.riot.routing import DEFAULT_OCE_PLATFORM_ROUTE
from league_api.riot.schemas import LeagueEntry


class LadderPageRiotClient(Protocol):
    async def fetch_ladder_page(
        self,
        queue: str,
        tier: str,
        division: str | None = None,
        page: int | None = None,
        platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE,
    ) -> list[LeagueEntry]: ...


class LadderPageIngestionResult(BaseModel):
    platform_route: str
    queue: str
    tier: str
    division: str | None
    page: int | None
    players_found: int
    unique_players: int
    entries: list[LeagueEntry]


class LadderPageIngestionService:
    def __init__(self, riot_client: LadderPageRiotClient) -> None:
        self._riot_client = riot_client

    async def ingest_ladder_page(
        self,
        queue: str,
        tier: str,
        division: str | None = None,
        page: int | None = None,
        platform_route: str = DEFAULT_OCE_PLATFORM_ROUTE,
    ) -> LadderPageIngestionResult:
        ladder_entries = await self._riot_client.fetch_ladder_page(
            queue=queue,
            tier=tier,
            division=division,
            page=page,
            platform_route=platform_route,
        )

        unique_puuids = list(dict.fromkeys(entry.puuid for entry in ladder_entries if entry.puuid))

        return LadderPageIngestionResult(
            platform_route=platform_route,
            queue=queue,
            tier=tier,
            division=division,
            page=page,
            players_found=len(ladder_entries),
            unique_players=len(unique_puuids),
            entries=ladder_entries,
        )
