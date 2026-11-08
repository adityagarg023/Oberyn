from __future__ import annotations

import asyncio
import logging
import signal
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.contracts.events import EventV1
from app.fingerprints import generate_fingerprint
from app.issues import persist_event_and_upsert_issue
from workers.config import Settings, load_settings

logger = logging.getLogger("oberyn.workers")


async def process_message(pool: asyncpg.Pool, fields: dict[str, str]) -> None:
    """Persist one stream event; return only after the PostgreSQL transaction commits."""
    project_id = fields["project_id"]
    organization_id = fields["organization_id"]
    event_id = fields["event_id"]
    event = EventV1.model_validate_json(fields["payload"])
    if str(event.event_id) != event_id:
        raise ValueError("stream event_id does not match payload event_id")

    await persist_event_and_upsert_issue(
        pool,
        project_id=UUID(project_id),
        organization_id=UUID(organization_id),
        event_id=event.event_id,
        payload_json=event.model_dump_json(),
        fingerprint=generate_fingerprint(event),
        title=f"{event.exception.type}: {event.exception.value}",
        occurred_at=event.timestamp,
        user_id=event.user.id if event.user is not None else None,
        release=event.release,
    )


async def _handle_message(
    redis: Redis,
    pool: asyncpg.Pool,
    settings: Settings,
    message_id: str,
    fields: dict[str, str],
) -> None:
    try:
        await process_message(pool, fields)
    except Exception:
        logger.exception("event processing failed stream_id=%s; leaving pending", message_id)
        return

    # The DB helper's transaction has committed before this acknowledgement.
    await redis.xack(settings.event_stream_name, settings.consumer_group, message_id)
    logger.debug("event processed and acknowledged stream_id=%s", message_id)


async def _process_messages(
    redis: Redis,
    pool: asyncpg.Pool,
    settings: Settings,
    messages: list[tuple[str, dict[str, str]]],
) -> None:
    for message_id, fields in messages:
        await _handle_message(redis, pool, settings, message_id, fields)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    logging.basicConfig(level=settings.log_level)
    if stop.is_set():
        return
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured for the worker")
    database_url = settings.database_url
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        try:
            await redis.xgroup_create(
                settings.event_stream_name,
                settings.consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        logger.info(
            "workers started environment=%s stream=%s group=%s consumer=%s",
            settings.environment,
            settings.event_stream_name,
            settings.consumer_group,
            settings.consumer_name,
        )
        claim_cursor = "0-0"
        while not stop.is_set():
            # Reclaim work abandoned by consumers that crashed or lost their lease.
            claimed = await redis.xautoclaim(
                settings.event_stream_name,
                settings.consumer_group,
                settings.consumer_name,
                settings.claim_idle_ms,
                start_id=claim_cursor,
                count=settings.read_count,
            )
            claim_cursor = claimed[0]
            claimed_messages = claimed[1]
            if claimed_messages:
                await _process_messages(redis, pool, settings, claimed_messages)

            if stop.is_set():
                break

            batches = await redis.xreadgroup(
                settings.consumer_group,
                settings.consumer_name,
                {settings.event_stream_name: ">"},
                count=settings.read_count,
                block=settings.block_ms,
            )
            for _stream, messages in batches:
                await _process_messages(redis, pool, settings, messages)
    finally:
        # Stop fetching new work; in-flight message processing completes before exit.
        await redis.aclose()
        await pool.close()
        logger.info("workers stopped")


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run(load_settings(), stop)


def cli() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    cli()
