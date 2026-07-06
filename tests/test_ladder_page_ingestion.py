import pytest

from league_api.ingestion.ladder_page import LadderPageIngestionService
from league_api.riot.schemas import LeagueEntry


class FakeRiotClient:
    def __init__(self) -> None:
        self.fetched_match_ids: list[str] = []

    async def fetch_ladder_page(
        self,
        queue: str,
        tier: str,
        division: str | None = None,
        page: int | None = None,
        platform_route: str = "oc1",
    ) -> list[LeagueEntry]:
        return [
            LeagueEntry(puuid="player-1"),
            LeagueEntry(puuid="player-2"),
            LeagueEntry(puuid="player-1"),
        ]


@pytest.mark.asyncio
async def test_ingestion_service_fetches_ladder_entries_and_counts_unique_players() -> None:
    service = LadderPageIngestionService(FakeRiotClient())

    result = await service.ingest_ladder_page(
        queue="RANKED_SOLO_5x5",
        tier="CHALLENGER",
    )

    assert result.players_found == 3
    assert result.unique_players == 2
    assert [entry.puuid for entry in result.entries] == ["player-1", "player-2", "player-1"]
