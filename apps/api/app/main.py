import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, load_settings
from app.contracts.events import validate_event_payload
from app.dashboard import router as dashboard_router
from app.db import engine


def get_database_engine() -> AsyncEngine:
    return engine


def get_redis_client(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_pg_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_url = settings.database_url
        if database_url.startswith("postgresql+asyncpg://"):
            database_url = database_url.replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )

        pg_pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=0,
            max_size=10,
            command_timeout=5,
        )
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.pg_pool = pg_pool
        app.state.redis = redis_client

        try:
            yield
        finally:
            await redis_client.aclose()
            await pg_pool.close()
            await engine.dispose()

    app = FastAPI(title="Oberyn API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.getenv("DASHBOARD_ORIGINS", "http://localhost:3000").split(
                ","
            )
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Oberyn-Key",
            "X-Oberyn-Project-Id",
            "X-Oberyn-SDK",
        ],
    )
    app.include_router(dashboard_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness(
        db_engine: AsyncEngine = Depends(get_database_engine),  # noqa: B008
        redis_client: Redis = Depends(get_redis_client),  # noqa: B008
    ) -> dict[str, str]:
        try:
            async with db_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc

        try:
            await redis_client.ping()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Redis unavailable") from exc

        return {"status": "ok", "database": "ok", "redis": "ok"}

    @app.post("/api/{project_id}/envelope", status_code=202)
    async def ingest_event(
        project_id: UUID,
        request: Request,
        project_key: str | None = Header(default=None, alias="X-Oberyn-Key"),
        header_project_id: str | None = Header(
            default=None, alias="X-Oberyn-Project-Id"
        ),
        pool: asyncpg.Pool = Depends(get_pg_pool),  # noqa: B008
        redis_client: Redis = Depends(get_redis_client),  # noqa: B008
    ) -> dict[str, Any]:
        if header_project_id is not None and header_project_id != str(project_id):
            raise HTTPException(status_code=401, detail="Invalid project credentials")
        if not project_key:
            raise HTTPException(status_code=401, detail="Invalid project credentials")

        key_hash = hashlib.sha256(project_key.encode("utf-8")).hexdigest()
        try:
            project = await pool.fetchrow(
                """
                SELECT p.id, p.organization_id
                FROM projects AS p
                INNER JOIN project_keys AS pk ON pk.project_id = p.id
                WHERE p.id = $1 AND pk.key_hash = $2 AND pk.revoked_at IS NULL
                LIMIT 1
                """,
                project_id,
                key_hash,
            )
        except asyncpg.PostgresError as exc:
            raise HTTPException(
                status_code=503, detail="Ingestion authentication unavailable"
            ) from exc
        if project is None:
            raise HTTPException(status_code=401, detail="Invalid project credentials")

        max_bytes = settings.event_limits.max_payload_bytes
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    raise HTTPException(status_code=413, detail="Event payload is too large")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise HTTPException(status_code=413, detail="Event payload is too large")

        try:
            event = validate_event_payload(bytes(body), settings.event_limits)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False, include_input=False),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        payload_json = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
        try:
            await redis_client.xadd(
                settings.event_stream_name,
                {
                    "project_id": str(project_id),
                    "organization_id": str(project["organization_id"]),
                    "event_id": str(event.event_id),
                    "payload": payload_json,
                },
            )
        except RedisError as exc:
            logger = logging.getLogger("oberyn.ingest")
            logger.exception("failed to enqueue event_id=%s", event.event_id)
            raise HTTPException(
                status_code=503, detail="Ingestion queue unavailable"
            ) from exc

        return {"status": "accepted", "event_id": str(event.event_id)}

    return app


app = create_app()
