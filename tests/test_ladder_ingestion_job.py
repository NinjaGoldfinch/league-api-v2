import asyncio
from types import TracebackType
from typing import Any

import pytest

from league_api.jobs.ingestion import run_ladder_ingestion
from league_api.jobs.models import JobStatus, JobType, LadderIngestionParams, LadderIngestionResult
from league_api.jobs.queue import InMemoryJobQueue
from league_api.jobs.store import InMemoryJobStore
from league_api.riot.errors import RiotApiError


class FakeRiotClient:
    def __init__(self, *, fail_match_detail: bool = False) -> None:
        self.fail_match_detail = fail_match_detail
        self.match_detail_calls: list[str] = []
        self.match_id_calls: list[str] = []

    async def __aenter__(self) -> "FakeRiotClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get_league_v4(
        self,
        path: str,
        *,
        platform_route: str = "oc1",
        params: dict[str, int | str | None] | None = None,
    ) -> dict[str, Any]:
        assert path == "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5"
        assert platform_route == "oc1"
        assert params is None
        return {
            "entries": [
                {"puuid": "puuid-1"},
                {"puuid": "puuid-2"},
                {"puuid": ""},
                {"summonerId": "legacy-id-without-puuid"},
            ]
        }

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str = "sea",
        params: dict[str, int | str | None] | None = None,
    ) -> list[str] | dict[str, Any]:
        assert regional_route == "sea"
        if path.endswith("/ids"):
            self.match_id_calls.append(path)
            assert params == {"start": 0, "count": 20}
            if "puuid-1" in path:
                return ["OC1_1", "OC1_2"]
            return ["OC1_2", "OC1_3"]

        self.match_detail_calls.append(path)
        if self.fail_match_detail:
            msg = "Riot API request failed with status 500."
            raise RiotApiError(msg, status_code=500)
        match_id = path.rsplit("/", maxsplit=1)[-1]
        return {"metadata": {"matchId": match_id}, "info": {}}


@pytest.mark.asyncio
async def test_ladder_ingestion_deduplicates_match_ids_before_fetching_details() -> None:
    store = InMemoryJobStore()
    job = await store.create_job(
        job_type=JobType.LADDER_INGESTION,
        params=LadderIngestionParams(),
    )
    fake_client = FakeRiotClient()

    result = await run_ladder_ingestion(
        LadderIngestionParams(),
        store,
        job.job_id,
        riot_client_factory=lambda: fake_client,
    )

    assert result.player_puuids == ["puuid-1", "puuid-2"]
    assert result.match_ids == ["OC1_1", "OC1_2", "OC1_3"]
    assert sorted(result.matches) == ["OC1_1", "OC1_2", "OC1_3"]
    assert result.summary.players_discovered == 2
    assert result.summary.players_processed == 2
    assert result.summary.match_ids_discovered == 4
    assert result.summary.unique_match_ids == 3
    assert result.summary.duplicate_match_ids_skipped == 1
    assert result.summary.matches_fetched == 3
    assert fake_client.match_detail_calls == [
        "/lol/match/v5/matches/OC1_1",
        "/lol/match/v5/matches/OC1_2",
        "/lol/match/v5/matches/OC1_3",
    ]


@pytest.mark.asyncio
async def test_failed_riot_call_marks_queue_job_failed() -> None:
    store = InMemoryJobStore()
    fake_client = FakeRiotClient(fail_match_detail=True)

    async def handler(
        params: LadderIngestionParams,
        store_arg: InMemoryJobStore,
        job_id: str,
    ) -> LadderIngestionResult:
        return await run_ladder_ingestion(
            params,
            store_arg,
            job_id,
            riot_client_factory=lambda: fake_client,
        )

    queue = InMemoryJobQueue(store=store, ladder_ingestion_handler=handler)
    queue.start()
    try:
        job = await store.create_job(
            job_type=JobType.LADDER_INGESTION,
            params=LadderIngestionParams(),
        )
        await queue.enqueue(job.job_id)

        for _ in range(50):
            record = await store.get_job(job.job_id)
            if record is not None and record.status is JobStatus.FAILED:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Job did not fail.")

        failed_record = await store.get_job(job.job_id)
        assert failed_record is not None
        assert failed_record.error is not None
        assert failed_record.error.error_type == "RiotApiError"
        assert failed_record.error.message == "Riot API request failed with status 500."
    finally:
        await queue.stop()
