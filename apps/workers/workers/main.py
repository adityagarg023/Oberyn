from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Sequence
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.contracts.events import EventV1
from workers.config import Settings, load_settings
from workers.persistence import persist_batch
from workers.processing import normalize_event

logger = logging.getLogger("oberyn.workers")
Message = tuple[str, dict[str, str]]


def _prepare_message(fields: dict[str, str]) -> tuple[UUID, UUID, EventV1]:
    project_id = UUID(fields["project_id"])
    organization_id = UUID(fields["organization_id"])
    event = EventV1.model_validate_json(fields["payload"])
    if str(event.event_id) != fields["event_id"]:
        raise ValueError("stream event_id does not match payload event_id")
    return project_id, organization_id, normalize_event(event)


async def _ack(redis: Redis, settings: Settings, messages: Sequence[Message]) -> None:
    if not messages:
        return
    pipeline = redis.pipeline(transaction=True)
    pipeline.xack(
        settings.event_stream_name,
        settings.consumer_group,
        *(message_id for message_id, _ in messages),
    )
    pipeline.hdel(
        settings.failure_hash_name,
        *(message_id for message_id, _ in messages),
    )
    await pipeline.execute()


async def _dead_letter(
    redis: Redis,
    settings: Settings,
    message_id: str,
    fields: dict[str, str],
    attempts: int,
    error: str,
) -> None:
    # XADD and XACK share a Redis transaction: never acknowledge before the
    # dead-letter copy is durably added to the destination stream.
    pipeline = redis.pipeline(transaction=True)
    pipeline.xadd(
        settings.dead_letter_stream_name,
        {
            **fields,
            "source_stream": settings.event_stream_name,
            "source_id": message_id,
            "failure_attempts": str(attempts),
            "failure_reason": error[:2000],
        },
    )
    pipeline.xack(settings.event_stream_name, settings.consumer_group, message_id)
    pipeline.hdel(settings.failure_hash_name, message_id)
    await pipeline.execute()
    logger.error("event moved to dead-letter stream stream_id=%s attempts=%d", message_id, attempts)


async def _record_failure(
    redis: Redis,
    settings: Settings,
    message: Message,
    error: Exception,
) -> int | None:
    message_id, fields = message
    attempts = int(await redis.hincrby(settings.failure_hash_name, message_id, 1))
    if attempts >= settings.max_attempts:
        await _dead_letter(redis, settings, message_id, fields, attempts, str(error))
        return None
    delay_ms = min(
        settings.retry_base_ms * (2 ** (attempts - 1)),
        settings.retry_max_ms,
    )
    logger.warning(
        "event processing failed stream_id=%s attempt=%d/%d retry_in_ms=%d",
        message_id,
        attempts,
        settings.max_attempts,
        delay_ms,
        exc_info=(type(error), error, error.__traceback__),
    )
    return delay_ms


async def process_messages(
    redis: Redis,
    pool: asyncpg.Pool,
    settings: Settings,
    messages: list[Message],
) -> None:
    """Process one bounded batch; database commit always precedes XACK."""
    prepared: list[tuple[Message, tuple[UUID, UUID, EventV1]]] = []
    delays: list[int] = []

    for message in messages:
        try:
            prepared.append((message, _prepare_message(message[1])))
        except Exception as error:
            delay = await _record_failure(redis, settings, message, error)
            if delay is not None:
                delays.append(delay)

    if not prepared:
        if delays:
            await asyncio.sleep(max(delays) / 1000)
        return

    try:
        await persist_batch(pool, [item[1] for item in prepared])
    except Exception as error:
        for message, _event in prepared:
            delay = await _record_failure(redis, settings, message, error)
            if delay is not None:
                delays.append(delay)
        if delays:
            await asyncio.sleep(max(delays) / 1000)
        return

    # A crash between commit and this call is safe: replay is deduplicated by
    # raw_events(project_id, event_id), so issue counters are not incremented.
    await _ack(redis, settings, [item[0] for item in prepared])
    logger.info("committed and acknowledged events=%d", len(prepared))


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
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

        logger.info(
            "worker started environment=%s stream=%s group=%s consumer=%s batch_size=%d",
            settings.environment,
            settings.event_stream_name,
            settings.consumer_group,
            settings.consumer_name,
            settings.batch_size,
        )
        claim_cursor = "0-0"
        while not stop.is_set():
            claimed = await redis.xautoclaim(
                settings.event_stream_name,
                settings.consumer_group,
                settings.consumer_name,
                settings.claim_idle_ms,
                start_id=claim_cursor,
                count=settings.batch_size,
            )
            claim_cursor, claimed_messages = claimed[0], claimed[1]
            if claimed_messages:
                await process_messages(redis, pool, settings, claimed_messages)

            if stop.is_set():
                break

            batches = await redis.xreadgroup(
                settings.consumer_group,
                settings.consumer_name,
                {settings.event_stream_name: ">"},
                count=settings.batch_size,
                block=settings.block_ms,
            )
            for _stream_name, stream_messages in batches:
                await process_messages(redis, pool, settings, stream_messages)
    finally:
        # No new reads are started after stop; any currently awaited transaction
        # finishes before resources close. Unacked work remains reclaimable.
        await redis.aclose()
        await pool.close()
        logger.info("worker stopped")


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
