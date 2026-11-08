from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from redis.asyncio import Redis

from workers.config import Settings
from workers.main import _handle_message, process_message

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
REDIS_URL = os.getenv("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not REDIS_URL,
    reason="Set TEST_DATABASE_URL and TEST_REDIS_URL for Redis/PostgreSQL integration tests",
)
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "event-v1.valid.json"
)


def postgres_dsn() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


async def create_project(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id, member_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,slug) VALUES($1,'stream-integration',$2)",
            organization_id,
            f"stream-integration-{organization_id}",
        )
        await connection.execute(
            "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
            member_id,
            organization_id,
            f"{member_id}@example.test",
        )
        await connection.execute(
            "UPDATE organizations SET owner_member_id=$1 WHERE id=$2",
            member_id,
            organization_id,
        )
        await connection.execute(
            """
            INSERT INTO projects(id,organization_id,created_by_member_id,name,slug)
            VALUES($1,$2,$3,'stream integration','stream-integration')
            """,
            project_id,
            organization_id,
            member_id,
        )
    return organization_id, project_id


@pytest.mark.asyncio
async def test_real_stream_delivery_and_postgres_persistence_are_idempotent() -> None:
    assert REDIS_URL is not None
    pool = await asyncpg.create_pool(postgres_dsn(), min_size=1, max_size=4)
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    organization_id, project_id = await create_project(pool)
    stream = f"events.raw.integration.{uuid.uuid4()}"
    group = "integration-workers"
    first_consumer = "consumer-before-crash"
    second_consumer = "consumer-after-crash"
    settings = Settings(
        database_url=postgres_dsn(),
        redis_url=REDIS_URL,
        event_stream_name=stream,
        consumer_group=group,
        consumer_name=second_consumer,
        claim_idle_ms=1,
    )

    try:
        payload: dict[str, Any] = json.loads(FIXTURE.read_text())
        event_id = uuid.uuid4()
        payload["event_id"] = str(event_id)
        fields = {
            "project_id": str(project_id),
            "organization_id": str(organization_id),
            "event_id": str(event_id),
            "payload": json.dumps(payload, separators=(",", ":")),
        }

        await redis.xadd(stream, fields)
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        first_batch = await redis.xreadgroup(
            group, first_consumer, {stream: ">"}, count=1, block=1000
        )
        assert first_batch
        message_id, delivered_fields = first_batch[0][1][0]

        # Simulate a worker crash after the DB commit but before XACK.
        await process_message(pool, delivered_fields)
        pending = await redis.xpending(stream, group)
        assert pending["pending"] == 1

        reclaimed = await redis.xautoclaim(
            stream, group, second_consumer, settings.claim_idle_ms, start_id="0-0", count=10
        )
        assert len(reclaimed[1]) == 1
        reclaimed_id, reclaimed_fields = reclaimed[1][0]
        assert reclaimed_id == message_id

        # Reprocessing must not increment issue counts or create a second raw event.
        await _handle_message(
            redis, pool, settings, reclaimed_id, reclaimed_fields
        )

        async with pool.acquire() as connection:
            raw_count = await connection.fetchval(
                "SELECT count(*) FROM raw_events WHERE project_id=$1 AND event_id=$2",
                project_id,
                event_id,
            )
            issue_count = await connection.fetchval(
                "SELECT event_count FROM issues WHERE project_id=$1",
                project_id,
            )
        assert raw_count == 1
        assert issue_count == 1
        assert (await redis.xpending(stream, group))["pending"] == 0
    finally:
        await redis.delete(stream)
        async with pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM organizations WHERE id=$1", organization_id
            )
        await redis.aclose()
        await pool.close()
