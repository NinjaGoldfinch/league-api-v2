import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from league_api.core.config import Settings


@dataclass(frozen=True, slots=True)
class RiotCacheKey:
    cache_key: str
    upstream_family: str
    route: str
    params_hash: str


@dataclass(frozen=True, slots=True)
class RiotCacheEntry:
    cache_key: str
    payload: Any
    status_code: int
    headers: dict[str, str]
    fetched_at: datetime
    expires_at: datetime
    stale_until: datetime

    def status_at(self, now: datetime | None = None) -> str | None:
        snapshot_at = now or datetime.now(UTC)
        if snapshot_at <= self.expires_at:
            return "hit"
        if snapshot_at <= self.stale_until:
            return "stale"
        return None


class RiotCacheStore(Protocol):
    async def get(self, cache_key: str) -> RiotCacheEntry | None: ...

    async def put(
        self,
        *,
        key: RiotCacheKey,
        payload: Any,
        status_code: int,
        headers: dict[str, str],
        ttl_seconds: int,
        stale_while_revalidate_seconds: int,
    ) -> RiotCacheEntry: ...


class InMemoryRiotCacheStore:
    def __init__(self) -> None:
        self._entries: dict[str, RiotCacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, cache_key: str) -> RiotCacheEntry | None:
        async with self._lock:
            return self._entries.get(cache_key)

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
        fetched_at = datetime.now(UTC)
        expires_at = fetched_at + timedelta(seconds=ttl_seconds)
        entry = RiotCacheEntry(
            cache_key=key.cache_key,
            payload=payload,
            status_code=status_code,
            headers=headers,
            fetched_at=fetched_at,
            expires_at=expires_at,
            stale_until=expires_at + timedelta(seconds=stale_while_revalidate_seconds),
        )
        async with self._lock:
            self._entries[key.cache_key] = entry
        return entry


def build_riot_cache_key(
    *,
    method: str,
    base_url: str,
    path: str,
    params: dict[str, int | str | None] | None,
) -> RiotCacheKey:
    filtered_params = {
        key: value for key, value in sorted((params or {}).items()) if value is not None
    }
    params_json = json.dumps(filtered_params, separators=(",", ":"), sort_keys=True)
    params_hash = hashlib.sha256(params_json.encode("utf-8")).hexdigest()
    host = urlparse(base_url).hostname or base_url
    upstream_family = _upstream_family(path)
    route = f"{method.upper()} {host}{path}"
    raw_key = json.dumps(
        {
            "method": method.upper(),
            "host": host,
            "path": path,
            "params": filtered_params,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return RiotCacheKey(
        cache_key=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        upstream_family=upstream_family,
        route=route,
        params_hash=params_hash,
    )


def ttl_for_riot_path(path: str, settings: Settings) -> int:
    if "/lol/match/v5/matches/by-puuid/" in path and path.endswith("/ids"):
        return settings.cache_match_ids_ttl_seconds
    if "/lol/match/v5/matches/" in path:
        return settings.cache_match_detail_ttl_seconds
    if "/riot/account/v1/" in path:
        return settings.cache_account_ttl_seconds
    if "/lol/summoner/v4/" in path:
        return settings.cache_summoner_ttl_seconds
    if "/lol/league/v4/entries/" in path:
        return settings.cache_league_entries_ttl_seconds
    if "/lol/league/v4/" in path:
        return settings.cache_ladder_ttl_seconds
    return settings.cache_default_ttl_seconds


def _upstream_family(path: str) -> str:
    if path.startswith("/riot/account/v1/"):
        return "account_v1"
    if path.startswith("/lol/summoner/v4/"):
        return "summoner_v4"
    if path.startswith("/lol/league/v4/"):
        return "league_v4"
    if path.startswith("/lol/match/v5/"):
        return "match_v5"
    return "riot"
