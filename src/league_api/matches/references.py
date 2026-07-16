import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, cast


class MatchReferenceStore(Protocol):
    async def upsert(self, puuid: str, match_ids: list[str]) -> None: ...
    async def counts_for_players(self, puuids: list[str]) -> dict[str, int]: ...
    async def list_counts_for_players(
        self,
        puuids: list[str],
        *,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[str, int]], int]: ...


class InMemoryMatchReferenceStore:
    def __init__(self) -> None:
        self._references: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, puuid: str, match_ids: list[str]) -> None:
        async with self._lock:
            self._references.setdefault(puuid, set()).update(match_ids)

    async def counts_for_players(self, puuids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        async with self._lock:
            for puuid in puuids:
                for match_id in self._references.get(puuid, set()):
                    counts[match_id] = counts.get(match_id, 0) + 1
        return counts

    async def list_counts_for_players(
        self,
        puuids: list[str],
        *,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[str, int]], int]:
        counts = await self.counts_for_players(puuids)
        needle = (search or "").casefold()
        items = [
            (match_id, count)
            for match_id, count in counts.items()
            if not needle or needle in match_id.casefold()
        ]
        items.sort(key=lambda item: (-item[1], item[0]))
        return items[offset : offset + limit], len(items)


class PostgresMatchReferenceStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def upsert(self, puuid: str, match_ids: list[str]) -> None:
        if not match_ids:
            return
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        query = text(
            """
            insert into player_match_references (puuid, match_id, discovered_at)
            select :puuid, match_id, :discovered_at
            from unnest(:match_ids) as match_id
            on conflict (puuid, match_id) do update set
              discovered_at=excluded.discovered_at
            """
        ).bindparams(bindparam("match_ids", type_=ARRAY(String)))
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "puuid": puuid,
                    "match_ids": match_ids,
                    "discovered_at": datetime.now(UTC),
                },
            )

    async def counts_for_players(self, puuids: list[str]) -> dict[str, int]:
        if not puuids:
            return {}
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        query = text(
            """
            select match_id, count(*) as player_count
            from player_match_references
            where puuid = any(:puuids)
            group by match_id
            """
        ).bindparams(bindparam("puuids", type_=ARRAY(String)))
        async with self._engine.begin() as conn:
            rows = (await conn.execute(query, {"puuids": puuids})).mappings().all()
        return {cast(str, row["match_id"]): int(row["player_count"]) for row in rows}

    async def list_counts_for_players(
        self,
        puuids: list[str],
        *,
        search: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[str, int]], int]:
        if not puuids:
            return [], 0
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        params = {
            "puuids": puuids,
            "search": f"%{(search or '').strip()}%",
            "offset": offset,
            "limit": limit,
        }
        grouped = """
            select match_id, count(*) as player_count
            from player_match_references
            where puuid = any(:puuids)
              and (:search='%%' or match_id ilike :search)
            group by match_id
        """
        bind = bindparam("puuids", type_=ARRAY(String))
        async with self._engine.begin() as conn:
            total = int(
                await conn.scalar(
                    text(f"select count(*) from ({grouped}) grouped").bindparams(bind), params
                )
                or 0
            )
            rows = (
                (
                    await conn.execute(
                        text(
                            f"""
                        {grouped}
                        order by player_count desc, match_id
                        offset :offset limit :limit
                        """
                        ).bindparams(bind),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return [(cast(str, row["match_id"]), int(row["player_count"])) for row in rows], total
