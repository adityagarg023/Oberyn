import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, load_settings
from app.db import engine


def get_database_engine() -> AsyncEngine:
    return engine


def get_redis_client(request: Request) -> Redis:
    return request.app.state.redis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis_client = from_url(
            settings.redis_url,
            decode_responses=True,
        )

        app.state.redis = redis_client

        try:
            yield
        finally:
            await redis_client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Oberyn API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    @app.get("/health/ready")
    async def readiness(
        db_engine: AsyncEngine = Depends(get_database_engine),  # noqa: B008
        redis_client: Redis = Depends(get_redis_client),  # noqa: B008
    ) -> dict[str, str]:
        try:
            async with db_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503,
                detail="Database unavailable",
            ) from exc

        try:
            await redis_client.ping()
        except RedisError as exc:
            raise HTTPException(
                status_code=503,
                detail="Redis unavailable",
            ) from exc

        return {
            "status": "ok",
            "database": "ok",
            "redis": "ok",
        }

    return app


app = create_app()
