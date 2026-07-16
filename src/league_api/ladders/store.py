import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast


@dataclass(frozen=True, slots=True)
class LadderPlayer:
    platform_route: str
    queue: str
    tier: str
    division: str | None
    page: int | None
    puuid: str
    league_points: int
    wins: int
    losses: int
    rank: str | None
    hot_streak: bool
    veteran: bool
    inactive: bool
    fresh_blood: bool
    game_name: str | None
    tag_line: str | None
    fetched_at: datetime


class LadderPlayerStore(Protocol):
    async def replace_target(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        players: list[LadderPlayer],
    ) -> None: ...
    async def get_identity(self, puuid: str) -> tuple[str, str] | None: ...

    async def list_puuids(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
    ) -> list[str]: ...
    async def list_players(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[LadderPlayer], int]: ...


class InMemoryLadderPlayerStore:
    def __init__(self) -> None:
        self._players: dict[tuple[str, str, str, str | None, int | None, str], LadderPlayer] = {}
        self._lock = asyncio.Lock()

    async def replace_target(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        players: list[LadderPlayer],
    ) -> None:
        target = (platform_route, queue, tier, division, page)
        async with self._lock:
            self._players = {
                key: value for key, value in self._players.items() if key[:5] != target
            }
            for player in players:
                key = (*target, player.puuid)
                self._players[key] = player

    async def get_identity(self, puuid: str) -> tuple[str, str] | None:
        async with self._lock:
            for player in self._players.values():
                if player.puuid == puuid and player.game_name and player.tag_line:
                    return player.game_name, player.tag_line
        return None

    async def list_puuids(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
    ) -> list[str]:
        target = (platform_route, queue, tier, division, page)
        async with self._lock:
            return [value.puuid for key, value in self._players.items() if key[:5] == target]

    async def list_players(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[LadderPlayer], int]:
        target = (platform_route, queue, tier, division, page)
        needle = (search or "").strip().casefold()
        async with self._lock:
            players = [value for key, value in self._players.items() if key[:5] == target]
        if needle:
            players = [
                p
                for p in players
                if needle in p.puuid.casefold()
                or needle in f"{p.game_name or ''}#{p.tag_line or ''}".casefold()
            ]
        players.sort(key=lambda p: (-p.league_points, -p.wins, p.puuid))
        return players[offset : offset + limit], len(players)


class PostgresLadderPlayerStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def replace_target(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        players: list[LadderPlayer],
    ) -> None:
        from sqlalchemy import text

        target = {
            "platform_route": platform_route,
            "queue": queue,
            "tier": tier,
            "division": division,
            "page": page,
        }
        delete = text(
            """
            delete from ranked_ladder_players
            where platform_route=:platform_route and queue=:queue and tier=:tier
              and division is not distinct from :division
              and page is not distinct from :page
            """
        )
        insert = text(
            """
            insert into ranked_ladder_players (
                platform_route, queue, tier, division, page, puuid, league_points,
                wins, losses, rank, hot_streak, veteran, inactive, fresh_blood,
                game_name, tag_line, fetched_at
            ) values (
                :platform_route, :queue, :tier, :division, :page, :puuid, :league_points,
                :wins, :losses, :rank, :hot_streak, :veteran, :inactive, :fresh_blood,
                :game_name, :tag_line, :fetched_at
            )
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(delete, target)
            if players:
                await conn.execute(insert, [asdict(player) for player in players])

    async def get_identity(self, puuid: str) -> tuple[str, str] | None:
        from sqlalchemy import text

        query = text(
            """
            select game_name, tag_line from ranked_ladder_players
            where puuid=:puuid and game_name is not null and tag_line is not null
            order by fetched_at desc limit 1
            """
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, {"puuid": puuid})).mappings().first()
        return (cast(str, row["game_name"]), cast(str, row["tag_line"])) if row else None

    async def list_puuids(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
    ) -> list[str]:
        from sqlalchemy import text

        query = text(
            """
            select puuid from ranked_ladder_players
            where platform_route=:platform_route and queue=:queue and tier=:tier
              and division is not distinct from :division
              and page is not distinct from :page
            """
        )
        async with self._engine.begin() as conn:
            rows = (
                (
                    await conn.execute(
                        query,
                        {
                            "platform_route": platform_route,
                            "queue": queue,
                            "tier": tier,
                            "division": division,
                            "page": page,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [cast(str, row["puuid"]) for row in rows]

    async def list_players(
        self,
        *,
        platform_route: str,
        queue: str,
        tier: str,
        division: str | None,
        page: int | None,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[LadderPlayer], int]:
        from sqlalchemy import text

        params = {
            "platform_route": platform_route,
            "queue": queue,
            "tier": tier,
            "division": division,
            "page": page,
            "search": f"%{(search or '').strip()}%",
            "offset": offset,
            "limit": limit,
        }
        where = """
            platform_route=:platform_route and queue=:queue and tier=:tier
            and division is not distinct from :division and page is not distinct from :page
            and (
                :search='%%' or puuid ilike :search
                or concat(coalesce(game_name,''),'#',coalesce(tag_line,'')) ilike :search
            )
        """
        async with self._engine.begin() as conn:
            total = int(
                await conn.scalar(
                    text(f"select count(*) from ranked_ladder_players where {where}"), params
                )
                or 0
            )
            rows = (
                (
                    await conn.execute(
                        text(f"""
                        select * from ranked_ladder_players where {where}
                        order by league_points desc, wins desc, puuid
                        limit :limit offset :offset
                    """),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return [
            LadderPlayer(
                platform_route=cast(str, row["platform_route"]),
                queue=cast(str, row["queue"]),
                tier=cast(str, row["tier"]),
                division=cast(str | None, row["division"]),
                page=cast(int | None, row["page"]),
                puuid=cast(str, row["puuid"]),
                league_points=cast(int, row["league_points"]),
                wins=cast(int, row["wins"]),
                losses=cast(int, row["losses"]),
                rank=cast(str | None, row["rank"]),
                hot_streak=cast(bool, row["hot_streak"]),
                veteran=cast(bool, row["veteran"]),
                inactive=cast(bool, row["inactive"]),
                fresh_blood=cast(bool, row["fresh_blood"]),
                game_name=cast(str | None, row["game_name"]),
                tag_line=cast(str | None, row["tag_line"]),
                fetched_at=_aware(cast(datetime, row["fetched_at"])),
            )
            for row in rows
        ], total


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
