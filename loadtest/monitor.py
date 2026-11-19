#!/usr/bin/env python3
"""Sample Redis consumer lag and PostgreSQL/worker progress during a k6 run.

Requires the apps/workers virtualenv (asyncpg and redis are already dependencies).
Emits one JSON object per sample to stdout; redirect to a JSONL artifact.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import asyncpg
from redis.asyncio import Redis


async def main() -> None:
    database_url = os.environ["DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    stream = os.getenv("EVENT_STREAM_NAME", "events.raw")
    group_name = os.getenv("EVENT_CONSUMER_GROUP", "oberyn-workers")
    interval = float(os.getenv("SAMPLE_INTERVAL_SECONDS", "1"))
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    previous_count: int | None = None
    try:
        while True:
            started = time.perf_counter()
            async with pool.acquire() as connection:
                db_started = time.perf_counter()
                await connection.fetchval("SELECT 1")
                db_latency_ms = (time.perf_counter() - db_started) * 1000
                event_count = await connection.fetchval("SELECT count(*) FROM raw_events")
            stream_length = await redis.xlen(stream)
            groups = await redis.xinfo_groups(stream) if stream_length else []
            group = next((item for item in groups if item.get("name") == group_name), {})
            pending = int(group.get("pending", 0))
            lag_value = group.get("lag")
            lag = int(lag_value) if lag_value is not None else None
            throughput = (
                None if previous_count is None
                else max(0, int(event_count) - previous_count) / interval
            )
            previous_count = int(event_count)
            print(json.dumps({
                "timestamp": datetime.now(UTC).isoformat(),
                "stream": stream,
                "stream_length": stream_length,
                "consumer_group": group_name,
                "consumer_group_lag": lag,
                "pending": pending,
                "persisted_raw_events": int(event_count),
                "worker_persisted_events_per_second": throughput,
                "database_select_1_latency_ms": round(db_latency_ms, 3),
            }), flush=True)
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0, interval - elapsed))
    finally:
        await redis.aclose()
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
