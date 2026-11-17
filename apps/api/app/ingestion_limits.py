"""Redis-backed, cross-process limits for ingestion and dashboard traffic.

All mutating counters are updated by Lua scripts so concurrent API workers share
one atomic decision. Redis errors are deliberately propagated: callers must fail
closed rather than silently bypassing abuse controls.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

DSN_CACHE_TTL_SECONDS = 60

TOKEN_BUCKET_LUA = """
local now = redis.call('TIME')
local current = tonumber(now[1]) + tonumber(now[2]) / 1000000
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens')) or tonumber(ARGV[1])
local updated = tonumber(redis.call('HGET', KEYS[1], 'updated')) or current
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
tokens = math.min(capacity, tokens + math.max(0, current - updated) * refill)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil((cost - tokens) / refill)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', current)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill * 2))
return {allowed, retry}
"""

FIXED_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
if count > tonumber(ARGV[2]) then
  return {0, redis.call('TTL', KEYS[1])}
end
return {1, 0}
"""

MONTHLY_QUOTA_LUA = """
local count = redis.call('INCRBY', KEYS[1], ARGV[1])
if count == tonumber(ARGV[1]) then redis.call('EXPIREAT', KEYS[1], ARGV[2]) end
if count > tonumber(ARGV[3]) then
  redis.call('DECRBY', KEYS[1], ARGV[1])
  return {0, count - tonumber(ARGV[1])}
end
return {1, count}
"""


@dataclass(frozen=True)
class ProjectCredentials:
    project_id: UUID
    organization_id: UUID
    cors_origins: tuple[str, ...] = ()


def dsn_cache_key(project_id: UUID, key_hash: str) -> str:
    return f"oberyn:dsn:{project_id}:{key_hash}"


async def invalidate_dsn_cache(redis: Redis, project_id: UUID, key_hash: str) -> None:
    """Invalidate a single credential after rotation/revocation."""
    await redis.delete(dsn_cache_key(project_id, key_hash))


async def resolve_project_key(
    pool: Any, redis: Redis, project_id: UUID, key_hash: str
) -> ProjectCredentials | None:
    cache_key = dsn_cache_key(project_id, key_hash)
    cached = await redis.get(cache_key)
    if cached:
        value = json.loads(cached)
        return ProjectCredentials(
            project_id=UUID(value["project_id"]),
            organization_id=UUID(value["organization_id"]),
            cors_origins=tuple(value.get("cors_origins", [])),
        )

    row = await pool.fetchrow(
        """
        SELECT p.id, p.organization_id, p.cors_origins
        FROM projects AS p
        INNER JOIN project_keys AS pk ON pk.project_id = p.id
        WHERE p.id = $1 AND pk.key_hash = $2 AND pk.revoked_at IS NULL
        LIMIT 1
        """,
        project_id,
        key_hash,
    )
    if row is None:
        return None

    credentials = ProjectCredentials(
        project_id=UUID(str(row["id"])),
        organization_id=UUID(str(row["organization_id"])),
        cors_origins=tuple(row.get("cors_origins") or []),
    )
    await redis.set(
        cache_key,
        json.dumps(
            {
                "project_id": str(credentials.project_id),
                "organization_id": str(credentials.organization_id),
                "cors_origins": credentials.cors_origins,
            }
        ),
        ex=DSN_CACHE_TTL_SECONDS,
    )
    return credentials


async def enforce_token_bucket(
    redis: Redis,
    project_key_hash: str,
    *,
    capacity: int,
    refill_per_second: float,
) -> None:
    result = await redis.eval(
        TOKEN_BUCKET_LUA,
        1,
        f"oberyn:limit:bucket:{project_key_hash}",
        capacity,
        refill_per_second,
        1,
    )
    if int(result[0]) != 1:
        raise HTTPException(
            status_code=429,
            detail="Project event rate limit exceeded",
            headers={"Retry-After": str(max(1, int(result[1])))},
        )


async def enforce_spike_limit(
    redis: Redis, project_id: UUID, *, window_seconds: int, max_events: int
) -> None:
    result = await redis.eval(
        FIXED_WINDOW_LUA,
        1,
        f"oberyn:limit:spike:{project_id}:{window_seconds}",
        window_seconds,
        max_events,
    )
    if int(result[0]) != 1:
        raise HTTPException(
            status_code=429,
            detail="Project spike protection limit exceeded",
            headers={"Retry-After": str(max(1, int(result[1])))},
        )


async def enforce_monthly_quota(
    redis: Redis,
    organization_id: UUID,
    *,
    monthly_limit: int,
    amount: int = 1,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    result = await redis.eval(
        MONTHLY_QUOTA_LUA,
        1,
        f"oberyn:quota:{organization_id}:{month_start:%Y-%m}",
        amount,
        math.ceil(next_month.timestamp()),
        monthly_limit,
    )
    if int(result[0]) != 1:
        raise HTTPException(
            status_code=429,
            detail="Organization monthly event quota exceeded",
            headers={"Retry-After": str(max(1, int((next_month - current).total_seconds())))},
        )


async def enforce_queue_backpressure(
    redis: Redis, stream_name: str, *, max_length: int
) -> None:
    length = int(await redis.xlen(stream_name))
    if length >= max_length:
        raise HTTPException(
            status_code=503,
            detail="Ingestion queue is overloaded",
            headers={"Retry-After": "5"},
        )


async def enforce_dashboard_limit(
    redis: Redis, identity: str, *, window_seconds: int, max_requests: int
) -> None:
    result = await redis.eval(
        FIXED_WINDOW_LUA,
        1,
        f"oberyn:limit:dashboard:{identity}",
        window_seconds,
        max_requests,
    )
    if int(result[0]) != 1:
        raise HTTPException(
            status_code=429,
            detail="Dashboard API rate limit exceeded",
            headers={"Retry-After": str(max(1, int(result[1])))},
        )
