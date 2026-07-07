import asyncio
from typing import Any, Protocol


class JobLockCoordinator(Protocol):
    async def acquire_job_lock(self, job_id: str, *, ttl_seconds: int = 900) -> str | None: ...

    async def release_job_lock(self, job_id: str, token: str) -> None: ...

    async def close(self) -> None: ...


class InMemoryJobLockCoordinator:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def acquire_job_lock(self, job_id: str, *, ttl_seconds: int = 900) -> str | None:
        del ttl_seconds
        async with self._lock:
            if job_id in self._tokens:
                return None
            token = f"in-memory:{job_id}"
            self._tokens[job_id] = token
            return token

    async def release_job_lock(self, job_id: str, token: str) -> None:
        async with self._lock:
            if self._tokens.get(job_id) == token:
                del self._tokens[job_id]

    async def close(self) -> None:
        return None


class RedisJobLockCoordinator:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def acquire_job_lock(self, job_id: str, *, ttl_seconds: int = 900) -> str | None:
        from uuid import uuid4

        token = str(uuid4())
        acquired = await self._redis.set(
            f"league-api:job-lock:{job_id}",
            token,
            nx=True,
            ex=ttl_seconds,
        )
        return token if acquired else None

    async def release_job_lock(self, job_id: str, token: str) -> None:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
        await self._redis.eval(script, 1, f"league-api:job-lock:{job_id}", token)

    async def close(self) -> None:
        await self._redis.aclose()


async def create_redis_client(redis_url: str) -> Any:
    from redis.asyncio import Redis

    return Redis.from_url(redis_url, decode_responses=True)
