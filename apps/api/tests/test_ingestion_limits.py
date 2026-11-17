from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis

from app.ingestion_limits import (
    dsn_cache_key,
    enforce_dashboard_limit,
    enforce_spike_limit,
    enforce_token_bucket,
    enqueue_guarded_event,
    invalidate_dsn_cache,
    resolve_project_key,
)

REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def redis_client() -> Any:
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis integration service is not available")
    yield redis
    await redis.aclose()


async def outcomes(calls: list[Any]) -> tuple[int, list[HTTPException]]:
    results = await asyncio.gather(*calls, return_exceptions=True)
    errors = [item for item in results if isinstance(item, HTTPException)]
    unexpected = [
        item
        for item in results
        if isinstance(item, BaseException) and not isinstance(item, HTTPException)
    ]
    assert not unexpected
    return len(results) - len(errors), errors


@pytest.mark.asyncio
async def test_token_bucket_is_atomic_under_concurrency(redis_client: Redis) -> None:
    key_hash = hashlib.sha256(str(uuid4()).encode()).hexdigest()
    allowed, rejected = await outcomes(
        [
            enforce_token_bucket(
                redis_client,
                key_hash,
                capacity=10,
                refill_per_second=0.0001,
            )
            for _ in range(50)
        ]
    )
    assert allowed == 10
    assert len(rejected) == 40
    assert all(error.status_code == 429 for error in rejected)
    assert all("Retry-After" in error.headers for error in rejected)


@pytest.mark.asyncio
async def test_spike_protection_is_atomic_under_concurrency(redis_client: Redis) -> None:
    project_id = uuid4()
    allowed, rejected = await outcomes(
        [
            enforce_spike_limit(
                redis_client,
                project_id,
                window_seconds=60,
                max_events=7,
            )
            for _ in range(40)
        ]
    )
    assert allowed == 7
    assert len(rejected) == 33
    assert all(error.status_code == 429 for error in rejected)


@pytest.mark.asyncio
async def test_monthly_quota_and_stream_backpressure_are_atomic(redis_client: Redis) -> None:
    stream = f"test:events:{uuid4()}"
    org_id, project_id = uuid4(), uuid4()
    allowed, rejected = await outcomes(
        [
            enqueue_guarded_event(
                redis_client,
                stream,
                project_id=project_id,
                organization_id=org_id,
                event_id=uuid4(),
                payload="{}",
                max_stream_length=100,
                monthly_limit=9,
            )
            for _ in range(50)
        ]
    )
    assert allowed == 9
    assert len(rejected) == 41
    assert all(error.status_code == 429 for error in rejected)
    assert await redis_client.xlen(stream) == 9
    await redis_client.delete(stream)


@pytest.mark.asyncio
async def test_queue_backpressure_returns_503_without_enqueueing(redis_client: Redis) -> None:
    stream = f"test:backpressure:{uuid4()}"
    await redis_client.xadd(stream, {"payload": "existing"})
    allowed, rejected = await outcomes(
        [
            enqueue_guarded_event(
                redis_client,
                stream,
                project_id=uuid4(),
                organization_id=uuid4(),
                event_id=uuid4(),
                payload="{}",
                max_stream_length=1,
                monthly_limit=100,
            )
            for _ in range(20)
        ]
    )
    assert allowed == 0
    assert len(rejected) == 20
    assert all(error.status_code == 503 for error in rejected)
    assert all(error.headers.get("Retry-After") == "5" for error in rejected)
    assert await redis_client.xlen(stream) == 1
    await redis_client.delete(stream)


@pytest.mark.asyncio
async def test_dashboard_rate_limit_is_atomic(redis_client: Redis) -> None:
    identity = str(uuid4())
    allowed, rejected = await outcomes(
        [
            enforce_dashboard_limit(
                redis_client,
                identity,
                window_seconds=60,
                max_requests=6,
            )
            for _ in range(30)
        ]
    )
    assert allowed == 6
    assert len(rejected) == 24
    assert all(error.status_code == 429 for error in rejected)


@pytest.mark.asyncio
async def test_dsn_cache_and_rotation_invalidation(redis_client: Redis) -> None:
    project_id, organization_id = uuid4(), uuid4()
    key_hash = hashlib.sha256(b"old-project-secret").hexdigest()

    class Pool:
        calls = 0
        revoked = False

        async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
            self.calls += 1
            if self.revoked:
                return None
            return {
                "id": project_id,
                "organization_id": organization_id,
                "cors_origins": '["https://example.com"]',
                "monthly_event_quota": 5000,
            }

    pool = Pool()
    first = await resolve_project_key(pool, redis_client, project_id, key_hash)
    second = await resolve_project_key(pool, redis_client, project_id, key_hash)
    assert first == second
    assert pool.calls == 1
    assert 0 < await redis_client.ttl(dsn_cache_key(project_id, key_hash)) <= 60
    assert first is not None
    assert first.monthly_event_quota == 5000
    assert first.cors_origins == ("https://example.com",)

    pool.revoked = True
    await invalidate_dsn_cache(redis_client, project_id, key_hash)
    third = await resolve_project_key(pool, redis_client, project_id, key_hash)
    assert third is None
    assert pool.calls == 2
