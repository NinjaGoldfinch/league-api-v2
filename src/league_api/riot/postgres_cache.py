from datetime import UTC, datetime, timedelta
from typing import Any, cast

from league_api.riot.cache import RiotCacheEntry, RiotCacheKey


class PostgresRiotCacheStore:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def get(self, cache_key: str) -> RiotCacheEntry | None:
        from sqlalchemy import text

        query = text(
            """
            select cache_key, payload, status_code, headers, fetched_at, expires_at, stale_until
            from riot_response_cache
            where cache_key = :cache_key
            """
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(query, {"cache_key": cache_key})).mappings().first()
        if row is None:
            return None
        return RiotCacheEntry(
            cache_key=cast(str, row["cache_key"]),
            payload=row["payload"],
            status_code=cast(int, row["status_code"]),
            headers=cast(dict[str, str], row["headers"] or {}),
            fetched_at=_aware(cast(datetime, row["fetched_at"])),
            expires_at=_aware(cast(datetime, row["expires_at"])),
            stale_until=_aware(cast(datetime, row["stale_until"])),
        )

    async def count(self) -> int:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            value = await conn.scalar(text("select count(*) from riot_response_cache"))
        return int(value or 0)

    async def delete(self, cache_key: str) -> bool:
        from sqlalchemy import text

        query = text("delete from riot_response_cache where cache_key = :cache_key")
        async with self._engine.begin() as conn:
            result = await conn.execute(query, {"cache_key": cache_key})
        return bool(result.rowcount)

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        from sqlalchemy import text

        query = text("delete from riot_response_cache where stale_until < :snapshot_at")
        async with self._engine.begin() as conn:
            result = await conn.execute(query, {"snapshot_at": now or datetime.now(UTC)})
        return int(result.rowcount or 0)

    async def put(
        self,
        *,
        key: RiotCacheKey,
        payload: Any,
        status_code: int,
        headers: dict[str, str],
        ttl_seconds: int,
        stale_while_revalidate_seconds: int,
    ) -> RiotCacheEntry:
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.sql import bindparam

        fetched_at = datetime.now(UTC)
        expires_at = fetched_at + timedelta(seconds=ttl_seconds)
        stale_until = expires_at + timedelta(seconds=stale_while_revalidate_seconds)
        query = text(
            """
            insert into riot_response_cache (
                cache_key, upstream_family, route, params_hash, payload, status_code,
                headers, fetched_at, expires_at, stale_until
            )
            values (
                :cache_key, :upstream_family, :route, :params_hash, :payload, :status_code,
                :headers, :fetched_at, :expires_at, :stale_until
            )
            on conflict (cache_key) do update set
                upstream_family = excluded.upstream_family,
                route = excluded.route,
                params_hash = excluded.params_hash,
                payload = excluded.payload,
                status_code = excluded.status_code,
                headers = excluded.headers,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at,
                stale_until = excluded.stale_until
            """
        ).bindparams(
            bindparam("payload", type_=JSONB),
            bindparam("headers", type_=JSONB),
        )
        prune_query = text(
            """
            delete from riot_response_cache
            where stale_until < :snapshot_at
              and cache_key <> :cache_key
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                prune_query,
                {"snapshot_at": fetched_at, "cache_key": key.cache_key},
            )
            await conn.execute(
                query,
                {
                    "cache_key": key.cache_key,
                    "upstream_family": key.upstream_family,
                    "route": key.route,
                    "params_hash": key.params_hash,
                    "payload": payload,
                    "status_code": status_code,
                    "headers": headers,
                    "fetched_at": fetched_at,
                    "expires_at": expires_at,
                    "stale_until": stale_until,
                },
            )
        return RiotCacheEntry(
            cache_key=key.cache_key,
            payload=payload,
            status_code=status_code,
            headers=headers,
            fetched_at=fetched_at,
            expires_at=expires_at,
            stale_until=stale_until,
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
