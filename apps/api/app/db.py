from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


def database_url() -> str:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://oberyn:change-me-local-only@localhost:5432/oberyn",
    )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

__all__ = ["Base", "SessionLocal", "engine"]
