import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast


@dataclass(frozen=True, slots=True)
class StoredMatch:
    match_id: str
    regional_route: str
    payload: dict[str, Any]
    game_creation: int | None
    fetched_at: datetime
    linked_puuids: list[str]


@dataclass(frozen=True, slots=True)
class MatchPage:
    matches: list[StoredMatch]
    total: int


class MatchStore(Protocol):
    async def get_player_match_ids(self, puuid: str) -> list[str]: ...

    async def get_matches(self, match_ids: list[str]) -> dict[str, dict[str, Any]]: ...

    async def save_match(
        self,
        match_id: str,
        *,
        regional_route: str,
        payload: dict[str, Any],
    ) -> None: ...

    async def link_player_matches(self, puuid: str, match_ids: list[str]) -> None: ...

    async def count_matches(self) -> int: ...

    async def count_player_links(self) -> int: ...

    async def list_matches(
        self, *, search: str | None, puuid: str | None, offset: int, limit: int
    ) -> MatchPage: ...

    async def get_match_record(self, match_id: str) -> StoredMatch | None: ...

    async def unlink_player_match(self, puuid: str, match_id: str) -> bool: ...

    async def delete_match(self, match_id: str) -> bool: ...


class InMemoryMatchStore:
    def __init__(self) -> None:
        self._matches: dict[str, StoredMatch] = {}
        self._player_match_ids: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def get_player_match_ids(self, puuid: str) -> list[str]:
        async with self._lock:
            return list(self._player_match_ids.get(puuid, []))

    async def get_matches(self, match_ids: list[str]) -> dict[str, dict[str, Any]]:
        async with self._lock:
            return {
                match_id: self._matches[match_id].payload.copy()
                for match_id in match_ids
                if match_id in self._matches
            }

    async def save_match(
        self,
        match_id: str,
        *,
        regional_route: str,
        payload: dict[str, Any],
    ) -> None:
        game_creation = payload.get("info", {}).get("gameCreation")
        async with self._lock:
            self._matches.setdefault(
                match_id,
                StoredMatch(
                    match_id=match_id,
                    regional_route=regional_route,
                    payload=payload.copy(),
                    game_creation=game_creation if isinstance(game_creation, int) else None,
                    fetched_at=datetime.now(UTC),
                    linked_puuids=[],
                ),
            )

    async def link_player_matches(self, puuid: str, match_ids: list[str]) -> None:
        async with self._lock:
            existing = self._player_match_ids.setdefault(puuid, [])
            existing_set = set(existing)
            new_ids = [match_id for match_id in match_ids if match_id not in existing_set]
            self._player_match_ids[puuid] = new_ids + existing

    async def count_matches(self) -> int:
        async with self._lock:
            return len(self._matches)

    async def count_player_links(self) -> int:
        async with self._lock:
            return sum(len(match_ids) for match_ids in self._player_match_ids.values())

    async def list_matches(
        self, *, search: str | None, puuid: str | None, offset: int, limit: int
    ) -> MatchPage:
        async with self._lock:
            allowed_ids = set(self._player_match_ids.get(puuid, [])) if puuid is not None else None
            records = [
                record
                for record in self._matches.values()
                if (allowed_ids is None or record.match_id in allowed_ids)
                and (search is None or search.lower() in record.match_id.lower())
            ]
            records.sort(
                key=lambda record: (record.game_creation or 0, record.match_id), reverse=True
            )
            hydrated = [self._with_links(record) for record in records[offset : offset + limit]]
            return MatchPage(matches=hydrated, total=len(records))

    async def get_match_record(self, match_id: str) -> StoredMatch | None:
        async with self._lock:
            record = self._matches.get(match_id)
            return self._with_links(record) if record is not None else None

    async def unlink_player_match(self, puuid: str, match_id: str) -> bool:
        async with self._lock:
            match_ids = self._player_match_ids.get(puuid, [])
            if match_id not in match_ids:
                return False
            self._player_match_ids[puuid] = [item for item in match_ids if item != match_id]
            return True

    async def delete_match(self, match_id: str) -> bool:
        async with self._lock:
            if self._matches.pop(match_id, None) is None:
                return False
            for puuid, match_ids in self._player_match_ids.items():
                self._player_match_ids[puuid] = [item for item in match_ids if item != match_id]
            return True

    def _with_links(self, record: StoredMatch) -> StoredMatch:
        linked_puuids = [
            puuid
            for puuid, match_ids in self._player_match_ids.items()
            if record.match_id in match_ids
        ]
        return StoredMatch(
            match_id=record.match_id,
            regional_route=record.regional_route,
            payload=record.payload.copy(),
            game_creation=record.game_creation,
            fetched_at=record.fetched_at,
            linked_puuids=linked_puuids,
        )


class PostgresMatchStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def get_player_match_ids(self, puuid: str) -> list[str]:
        from sqlalchemy import text

        query = text(
            """
            select pm.match_id
            from player_matches pm
            join riot_matches m on m.match_id = pm.match_id
            where pm.puuid = :puuid
            order by m.game_creation desc nulls last, pm.discovered_at desc, pm.match_id desc
            """
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(query, {"puuid": puuid})).mappings().all()
        return [cast(str, row["match_id"]) for row in rows]

    async def get_matches(self, match_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not match_ids:
            return {}
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        query = text(
            """
            select match_id, payload
            from riot_matches
            where match_id = any(:match_ids)
            """
        ).bindparams(bindparam("match_ids", type_=ARRAY(String)))
        async with self._engine.begin() as conn:
            rows = (await conn.execute(query, {"match_ids": match_ids})).mappings().all()
        return {cast(str, row["match_id"]): cast(dict[str, Any], row["payload"]) for row in rows}

    async def save_match(
        self,
        match_id: str,
        *,
        regional_route: str,
        payload: dict[str, Any],
    ) -> None:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        game_creation = payload.get("info", {}).get("gameCreation")
        query = text(
            """
            insert into riot_matches (
                match_id, regional_route, payload, game_creation, fetched_at
            ) values (
                :match_id, :regional_route, :payload, :game_creation, :fetched_at
            )
            on conflict (match_id) do nothing
            """
        ).bindparams(bindparam("payload", type_=JSONB))
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                {
                    "match_id": match_id,
                    "regional_route": regional_route,
                    "payload": payload,
                    "game_creation": game_creation if isinstance(game_creation, int) else None,
                    "fetched_at": datetime.now(UTC),
                },
            )

    async def link_player_matches(self, puuid: str, match_ids: list[str]) -> None:
        if not match_ids:
            return
        from sqlalchemy import String, text
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy.sql import bindparam

        query = text(
            """
            insert into player_matches (puuid, match_id)
            select :puuid, match_id
            from unnest(:match_ids) as match_id
            on conflict (puuid, match_id) do nothing
            """
        ).bindparams(bindparam("match_ids", type_=ARRAY(String)))
        async with self._engine.begin() as conn:
            await conn.execute(query, {"puuid": puuid, "match_ids": match_ids})

    async def count_matches(self) -> int:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            value = await conn.scalar(text("select count(*) from riot_matches"))
        return int(value or 0)

    async def count_player_links(self) -> int:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            value = await conn.scalar(text("select count(*) from player_matches"))
        return int(value or 0)

    async def list_matches(
        self, *, search: str | None, puuid: str | None, offset: int, limit: int
    ) -> MatchPage:
        from sqlalchemy import text

        where = ["(:search is null or m.match_id ilike '%' || :search || '%')"]
        if puuid is not None:
            where.append(
                "exists (select 1 from player_matches filter_pm "
                "where filter_pm.match_id = m.match_id and filter_pm.puuid = :puuid)"
            )
        where_sql = " and ".join(where)
        params = {"search": search, "puuid": puuid, "offset": offset, "limit": limit}
        count_query = text(f"select count(*) from riot_matches m where {where_sql}")
        query = text(
            f"""
            select m.match_id, m.regional_route, m.payload, m.game_creation, m.fetched_at,
                   coalesce(array_agg(pm.puuid) filter (where pm.puuid is not null), '{{}}')
                     as linked_puuids
            from riot_matches m
            left join player_matches pm on pm.match_id = m.match_id
            where {where_sql}
            group by m.match_id
            order by m.game_creation desc nulls last, m.match_id desc
            offset :offset limit :limit
            """
        )
        async with self._engine.begin() as conn:
            total = int(await conn.scalar(count_query, params) or 0)
            rows = (await conn.execute(query, params)).mappings().all()
        return MatchPage(matches=[self._record(row) for row in rows], total=total)

    async def get_match_record(self, match_id: str) -> StoredMatch | None:
        from sqlalchemy import text

        query = text(
            """
            select m.match_id, m.regional_route, m.payload, m.game_creation, m.fetched_at,
                   coalesce(array_agg(pm.puuid) filter (where pm.puuid is not null), '{}')
                     as linked_puuids
            from riot_matches m
            left join player_matches pm on pm.match_id = m.match_id
            where m.match_id = :match_id
            group by m.match_id
            """
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, {"match_id": match_id})).mappings().first()
        return self._record(row) if row is not None else None

    async def unlink_player_match(self, puuid: str, match_id: str) -> bool:
        from sqlalchemy import text

        query = text("delete from player_matches where puuid = :puuid and match_id = :match_id")
        async with self._engine.begin() as conn:
            result = await conn.execute(query, {"puuid": puuid, "match_id": match_id})
        return bool(result.rowcount)

    async def delete_match(self, match_id: str) -> bool:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("delete from riot_matches where match_id = :match_id"),
                {"match_id": match_id},
            )
        return bool(result.rowcount)

    @staticmethod
    def _record(row: Any) -> StoredMatch:
        return StoredMatch(
            match_id=cast(str, row["match_id"]),
            regional_route=cast(str, row["regional_route"]),
            payload=cast(dict[str, Any], row["payload"]),
            game_creation=cast(int | None, row["game_creation"]),
            fetched_at=_aware(cast(datetime, row["fetched_at"])),
            linked_puuids=list(row["linked_puuids"] or []),
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
