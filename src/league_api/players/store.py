import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast


@dataclass(frozen=True, slots=True)
class PlayerIdentity:
    puuid: str
    game_name: str
    tag_line: str
    observed_at: datetime


class PlayerIdentityStore(Protocol):
    async def upsert(self, identity: PlayerIdentity) -> None: ...
    async def get_by_puuid(
        self, puuid: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None: ...
    async def get_by_riot_id(
        self, game_name: str, tag_line: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None: ...


class InMemoryPlayerIdentityStore:
    def __init__(self) -> None:
        self._identities: dict[str, PlayerIdentity] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, identity: PlayerIdentity) -> None:
        async with self._lock:
            current = self._identities.get(identity.puuid)
            if current is None or identity.observed_at >= current.observed_at:
                self._identities[identity.puuid] = identity

    async def get_by_puuid(
        self, puuid: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None:
        cutoff = datetime.now(UTC) - max_age if max_age is not None else None
        async with self._lock:
            identity = self._identities.get(puuid)
        if identity is not None and (cutoff is None or identity.observed_at >= cutoff):
            return identity
        return None

    async def get_by_riot_id(
        self, game_name: str, tag_line: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None:
        cutoff = datetime.now(UTC) - max_age if max_age is not None else None
        needle = (game_name.strip().casefold(), tag_line.strip().casefold())
        async with self._lock:
            matches = [
                identity
                for identity in self._identities.values()
                if (identity.game_name.casefold(), identity.tag_line.casefold()) == needle
                and (cutoff is None or identity.observed_at >= cutoff)
            ]
        return max(matches, key=lambda item: item.observed_at, default=None)


class PostgresPlayerIdentityStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def upsert(self, identity: PlayerIdentity) -> None:
        from sqlalchemy import text

        query = text("""
            insert into player_identities (puuid, game_name, tag_line, observed_at)
            values (:puuid, :game_name, :tag_line, :observed_at)
            on conflict (puuid) do update set
              game_name=excluded.game_name, tag_line=excluded.tag_line,
              observed_at=excluded.observed_at
            where excluded.observed_at >= player_identities.observed_at
        """)
        async with self._engine.begin() as conn:
            await conn.execute(
                query,
                identity.__dict__
                if hasattr(identity, "__dict__")
                else {
                    "puuid": identity.puuid,
                    "game_name": identity.game_name,
                    "tag_line": identity.tag_line,
                    "observed_at": identity.observed_at,
                },
            )

    async def get_by_puuid(
        self, puuid: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None:
        from sqlalchemy import text

        cutoff = datetime.now(UTC) - max_age if max_age is not None else None
        age_filter = " and observed_at >= :cutoff" if cutoff is not None else ""
        query = text(
            """
            select puuid, game_name, tag_line, observed_at
            from player_identities where puuid=:puuid
            """
            + age_filter
        )
        query_params: dict[str, Any] = {"puuid": puuid}
        if cutoff is not None:
            query_params["cutoff"] = cutoff
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, query_params)).mappings().first()
        if row is None:
            return None
        observed_at = row["observed_at"]
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return PlayerIdentity(row["puuid"], row["game_name"], row["tag_line"], observed_at)

    async def get_by_riot_id(
        self, game_name: str, tag_line: str, *, max_age: timedelta | None = None
    ) -> PlayerIdentity | None:
        from sqlalchemy import text

        cutoff = datetime.now(UTC) - max_age if max_age is not None else None
        age_filter = " and observed_at >= :cutoff" if cutoff is not None else ""
        query = text(
            """
            select puuid, game_name, tag_line, observed_at from player_identities
            where lower(game_name)=:game_name and lower(tag_line)=:tag_line
            """
            + age_filter
            + " order by observed_at desc limit 1"
        )
        query_params: dict[str, Any] = {
            "game_name": game_name.strip().casefold(),
            "tag_line": tag_line.strip().casefold(),
        }
        if cutoff is not None:
            query_params["cutoff"] = cutoff
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, query_params)).mappings().first()
        if row is None:
            return None
        observed_at = row["observed_at"]
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return PlayerIdentity(row["puuid"], row["game_name"], row["tag_line"], observed_at)


def identities_from_match(payload: dict[str, Any]) -> list[PlayerIdentity]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return []
    created = info.get("gameCreation")
    if not isinstance(created, (int, float)):
        return []
    observed_at = datetime.fromtimestamp(created / 1000, tz=UTC)
    participants = info.get("participants")
    if not isinstance(participants, list):
        return []
    identities: list[PlayerIdentity] = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        puuid = participant.get("puuid")
        game_name = participant.get("riotIdGameName")
        tag_line = participant.get("riotIdTagline")
        if all(isinstance(value, str) and value for value in (puuid, game_name, tag_line)):
            identities.append(
                PlayerIdentity(
                    cast(str, puuid), cast(str, game_name), cast(str, tag_line), observed_at
                )
            )
    return identities


async def hydrate_identities(store: PlayerIdentityStore, payload: dict[str, Any]) -> None:
    for identity in identities_from_match(payload):
        await store.upsert(identity)
