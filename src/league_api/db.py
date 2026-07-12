from typing import Any


def create_async_engine_from_url(database_url: str) -> Any:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(database_url, pool_pre_ping=True)
