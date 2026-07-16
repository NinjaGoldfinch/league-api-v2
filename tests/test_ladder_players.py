from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any

import pytest

from league_api.jobs.ladder_players import run_ladder_players_fetch
from league_api.jobs.models import (
    JobType,
    LadderFetchMode,
    LadderPlayersParams,
    RankedDivision,
    RankedTier,
)
from league_api.jobs.store import InMemoryJobStore
from league_api.ladders.store import InMemoryLadderPlayerStore, LadderPlayer
from league_api.matches.references import InMemoryMatchReferenceStore
from league_api.matches.store import InMemoryMatchStore
from league_api.players.store import InMemoryPlayerIdentityStore, PlayerIdentity
from league_api.riot.rate_limiter import RiotRateLimitAudience


class FakeLadderClient:
    def __init__(self) -> None:
        self.league_calls: list[tuple[str, dict[str, int | str | None] | None]] = []
        self.account_calls: list[str] = []

    async def __aenter__(self) -> "FakeLadderClient":
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
        platform_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        assert platform_route == "oc1"
        assert rate_limit_audience is RiotRateLimitAudience.AUTOMATIC
        self.league_calls.append((path, params))
        entries = [
            {"puuid": "known", "leaguePoints": 500, "wins": 10, "losses": 2},
            {"puuid": "new", "leaguePoints": 400, "wins": 8, "losses": 4},
        ]
        return {"entries": entries} if "challengerleagues" in path else entries

    async def get_account_v1(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        assert regional_route == "asia"
        assert rate_limit_audience is RiotRateLimitAudience.AUTOMATIC
        self.account_calls.append(path)
        return {"puuid": "new", "gameName": "New Player", "tagLine": "OCE"}

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        raise AssertionError("Ladder-only jobs must not call Match-V5.")


class CombinedLadderClient(FakeLadderClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, int | str | None] | None]] = []
        self.new_start_failures = 0

    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        assert regional_route == "sea"
        self.calls.append((path, params))
        if path.endswith("/ids"):
            assert params is not None
            assert params["count"] == 100
            if "/known/" in path and params["start"] == 0:
                return ["shared", *[f"known-{index}" for index in range(99)]]
            if "/known/" in path and params["start"] == 100:
                return ["known-tail"]
            if "/new/" in path and params["start"] == 0:
                if self.new_start_failures == 0:
                    self.new_start_failures += 1
                    raise RuntimeError("temporary ID page failure")
                return ["shared", "new-only"]
            raise AssertionError(f"Unexpected ID page: {path} {params}")
        match_id = path.rsplit("/", maxsplit=1)[-1]
        return {
            "metadata": {"matchId": match_id},
            "info": {"gameCreation": 1, "participants": []},
        }


class ExhaustedRetryClient(CombinedLadderClient):
    async def get_match_v5(
        self,
        path: str,
        *,
        regional_route: str,
        params: dict[str, int | str | None] | None = None,
        rate_limit_audience: RiotRateLimitAudience = RiotRateLimitAudience.MANUAL,
        wait_for_rate_limit: bool = True,
    ) -> Any:
        if path.endswith("/ids") and "/new/" in path:
            self.calls.append((path, params))
            raise RuntimeError("persistent ID page failure")
        if path.endswith("/ids") and "/known/" in path:
            self.calls.append((path, params))
            return ["known-only"]
        return await super().get_match_v5(
            path,
            regional_route=regional_route,
            params=params,
            rate_limit_audience=rate_limit_audience,
            wait_for_rate_limit=wait_for_rate_limit,
        )


@pytest.mark.asyncio
async def test_fetches_apex_players_and_reuses_stored_identity() -> None:
    store = InMemoryJobStore()
    players = InMemoryLadderPlayerStore()
    await players.replace_target(
        platform_route="oc1",
        queue="RANKED_SOLO_5x5",
        tier="MASTER",
        division=None,
        page=None,
        players=[_stored_player("known", "Known Player", "OCE")],
    )
    params = LadderPlayersParams()
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = FakeLadderClient()

    result = await run_ladder_players_fetch(
        params,
        store,
        job.job_id,
        player_store=players,
        riot_client_factory=lambda **_: client,
    )

    assert client.league_calls == [
        ("/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5", None)
    ]
    assert client.account_calls == ["/riot/account/v1/accounts/by-puuid/new"]
    assert result.player_puuids == ["known", "new"]
    assert result.summary.identities_reused == 1
    assert result.summary.identities_resolved == 1
    page, total = await players.list_players(
        platform_route="oc1",
        queue="RANKED_SOLO_5x5",
        tier="CHALLENGER",
        division=None,
        page=None,
        search=None,
        offset=0,
        limit=100,
    )
    assert total == 2
    assert [(player.puuid, player.game_name) for player in page] == [
        ("known", "Known Player"),
        ("new", "New Player"),
    ]


@pytest.mark.asyncio
async def test_ladder_players_publish_one_complete_snapshot() -> None:
    store = InMemoryJobStore()
    players = RecordingLadderPlayerStore()
    params = LadderPlayersParams()
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)

    await run_ladder_players_fetch(
        params,
        store,
        job.job_id,
        player_store=players,
        riot_client_factory=lambda **_: FakeLadderClient(),
    )

    assert players.persisted_counts == [2]


@pytest.mark.asyncio
async def test_ladder_reuses_match_identity_observed_within_24_hours() -> None:
    store = InMemoryJobStore()
    players = InMemoryLadderPlayerStore()
    identities = InMemoryPlayerIdentityStore()
    await identities.upsert(PlayerIdentity("known", "Match Name", "OCE", datetime.now(UTC)))
    params = LadderPlayersParams()
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = FakeLadderClient()

    result = await run_ladder_players_fetch(
        params,
        store,
        job.job_id,
        player_store=players,
        identity_store=identities,
        riot_client_factory=lambda **_: client,
    )

    assert client.account_calls == ["/riot/account/v1/accounts/by-puuid/new"]
    assert result.summary.identities_reused == 1


@pytest.mark.asyncio
async def test_ladder_refreshes_match_identity_older_than_24_hours() -> None:
    store = InMemoryJobStore()
    players = InMemoryLadderPlayerStore()
    identities = InMemoryPlayerIdentityStore()
    await identities.upsert(
        PlayerIdentity("known", "Old Match Name", "OCE", datetime.now(UTC) - timedelta(hours=25))
    )
    params = LadderPlayersParams()
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = FakeLadderClient()

    await run_ladder_players_fetch(
        params,
        store,
        job.job_id,
        player_store=players,
        identity_store=identities,
        riot_client_factory=lambda **_: client,
    )

    assert client.account_calls == [
        "/riot/account/v1/accounts/by-puuid/known",
        "/riot/account/v1/accounts/by-puuid/new",
    ]


@pytest.mark.asyncio
async def test_fetches_exact_lower_tier_page() -> None:
    store = InMemoryJobStore()
    players = InMemoryLadderPlayerStore()
    params = LadderPlayersParams(
        tier=RankedTier.PLATINUM,
        division=RankedDivision.DIVISION_I,
        page=3,
    )
    job = await store.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = FakeLadderClient()

    await run_ladder_players_fetch(
        params,
        store,
        job.job_id,
        player_store=players,
        riot_client_factory=lambda **_: client,
    )

    assert client.league_calls == [
        ("/lol/league/v4/entries/RANKED_SOLO_5x5/PLATINUM/I", {"page": 3})
    ]


def test_ladder_target_validation() -> None:
    with pytest.raises(ValueError, match="Apex ladders"):
        LadderPlayersParams(tier=RankedTier.CHALLENGER, page=1)
    with pytest.raises(ValueError, match="Lower-tier"):
        LadderPlayersParams(tier=RankedTier.PLATINUM)


@pytest.mark.asyncio
async def test_combined_job_discovers_retries_deduplicates_then_fetches_details() -> None:
    jobs = InMemoryJobStore()
    players = InMemoryLadderPlayerStore()
    matches = InMemoryMatchStore()
    references = InMemoryMatchReferenceStore()
    params = LadderPlayersParams(mode=LadderFetchMode.LADDER_AND_MATCHES)
    job = await jobs.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = CombinedLadderClient()
    await matches.save_match(
        "shared",
        regional_route="sea",
        payload={"metadata": {"matchId": "shared"}, "info": {"gameCreation": 1}},
    )

    result = await run_ladder_players_fetch(
        params,
        jobs,
        job.job_id,
        player_store=players,
        match_store=matches,
        match_reference_store=references,
        riot_client_factory=lambda **_: client,
    )

    id_call_indexes = [
        index for index, (path, _) in enumerate(client.calls) if path.endswith("/ids")
    ]
    detail_call_indexes = [
        index for index, (path, _) in enumerate(client.calls) if not path.endswith("/ids")
    ]
    assert detail_call_indexes
    assert max(id_call_indexes) < min(detail_call_indexes)
    assert client.calls[:4] == [
        ("/lol/match/v5/matches/by-puuid/known/ids", {"start": 0, "count": 100}),
        ("/lol/match/v5/matches/by-puuid/known/ids", {"start": 100, "count": 100}),
        ("/lol/match/v5/matches/by-puuid/new/ids", {"start": 0, "count": 100}),
        ("/lol/match/v5/matches/by-puuid/new/ids", {"start": 0, "count": 100}),
    ]
    assert result.summary.match_id_pages_retried == 1
    assert result.summary.match_details_reused == 1
    assert result.summary.unique_match_ids == 102
    assert result.summary.duplicate_match_references == 1
    counts = await references.counts_for_players(["known", "new"])
    assert counts["shared"] == 2
    assert len(result.match_ids) == 102


@pytest.mark.asyncio
async def test_combined_job_uses_three_retry_passes_then_fetches_known_ids() -> None:
    jobs = InMemoryJobStore()
    params = LadderPlayersParams(mode=LadderFetchMode.LADDER_AND_MATCHES)
    job = await jobs.create_job(job_type=JobType.LADDER_PLAYERS, params=params)
    client = ExhaustedRetryClient()

    result = await run_ladder_players_fetch(
        params,
        jobs,
        job.job_id,
        player_store=InMemoryLadderPlayerStore(),
        match_store=InMemoryMatchStore(),
        match_reference_store=InMemoryMatchReferenceStore(),
        riot_client_factory=lambda **_: client,
    )

    failed_calls = [path for path, _ in client.calls if "/new/ids" in path]
    assert len(failed_calls) == 4
    assert result.summary.match_id_pages_retried == 3
    assert result.match_ids == ["known-only"]
    assert any(error.player_puuid == "new" for error in result.errors)


def _stored_player(puuid: str, game_name: str, tag_line: str) -> LadderPlayer:
    return LadderPlayer(
        platform_route="oc1",
        queue="RANKED_SOLO_5x5",
        tier="MASTER",
        division=None,
        page=None,
        puuid=puuid,
        league_points=0,
        wins=0,
        losses=0,
        rank=None,
        hot_streak=False,
        veteran=False,
        inactive=False,
        fresh_blood=False,
        game_name=game_name,
        tag_line=tag_line,
        fetched_at=datetime.now(UTC),
    )


class RecordingLadderPlayerStore(InMemoryLadderPlayerStore):
    def __init__(self) -> None:
        super().__init__()
        self.persisted_counts: list[int] = []

    async def replace_target(self, **kwargs: Any) -> None:
        self.persisted_counts.append(len(kwargs["players"]))
        await super().replace_target(**kwargs)
