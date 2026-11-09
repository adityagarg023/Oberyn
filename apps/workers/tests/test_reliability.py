from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.contracts.events import EventV1
from app.fingerprints import generate_fingerprint
from workers.config import Settings
from workers.main import process_messages
from workers.processing import normalize_event, scrub_raw_payload


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.snapshot: tuple[Any, ...] | None = None

    async def __aenter__(self) -> None:
        self.snapshot = self.connection.snapshot()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None and self.snapshot is not None:
            self.connection.restore(self.snapshot)
        return False


class FakeConnection:
    def __init__(self) -> None:
        self.raw_ids: set[tuple[UUID, UUID]] = set()
        self.issue_counts: dict[tuple[UUID, str], int] = {}
        self.event_rows: set[tuple[UUID, UUID]] = set()
        self.event_inserts = 0
        self.crash_after: int | None = None

    def snapshot(self) -> tuple[Any, ...]:
        return (
            deepcopy(self.raw_ids),
            deepcopy(self.issue_counts),
            deepcopy(self.event_rows),
            self.event_inserts,
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        self.raw_ids, self.issue_counts, self.event_rows, self.event_inserts = snapshot

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any]:
        project_id, _organization_id, event_id = args[:3]
        fingerprint = args[4]
        issue_key = (project_id, fingerprint)
        key = (project_id, event_id)
        if key in self.raw_ids:
            return {"inserted_event_id": None, "issue_id": None}
        self.raw_ids.add(key)
        self.issue_counts[issue_key] = self.issue_counts.get(issue_key, 0) + 1
        return {"inserted_event_id": event_id, "issue_id": uuid4()}

    async def execute(self, query: str, *args: Any) -> str:
        if query.lstrip().startswith("UPDATE raw_events"):
            return "UPDATE 1"
        self.event_rows.add((args[2], args[0]))
        self.event_inserts += 1
        if self.crash_after == self.event_inserts:
            raise asyncio.CancelledError("simulated worker termination")
        return "INSERT 0 1"


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.acks: list[str] = []
        self.hash_deletes: list[str] = []
        self.dead_letters: list[dict[str, str]] = []

    def xack(self, _stream: str, _group: str, *ids: str) -> None:
        self.acks.extend(ids)

    def hdel(self, _key: str, *ids: str) -> None:
        self.hash_deletes.extend(ids)

    def xadd(self, _stream: str, fields: dict[str, str]) -> None:
        self.dead_letters.append(fields)

    async def execute(self) -> list[Any]:
        self.redis.acked.extend(self.acks)
        for message_id in self.hash_deletes:
            self.redis.failures.pop(message_id, None)
        self.redis.dead_letters.extend(self.dead_letters)
        return []


class FakeRedis:
    def __init__(self) -> None:
        self.acked: list[str] = []
        self.failures: dict[str, int] = {}
        self.dead_letters: list[dict[str, str]] = []

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def hincrby(self, _key: str, message_id: str, amount: int) -> int:
        self.failures[message_id] = self.failures.get(message_id, 0) + amount
        return self.failures[message_id]


def make_event(event_id: UUID | None = None) -> EventV1:
    return EventV1.model_validate(
        {
            "schema_version": 1,
            "event_id": event_id or uuid4(),
            "timestamp": datetime.now(UTC),
            "platform": "python",
            "level": "error",
            "exception": {
                "type": "ValueError",
                "value": "invalid input",
                "stacktrace": [
                    {
                        "filename": " app/main.py ",
                        "function": "  handle_request  ",
                        "lineno": 12,
                        "colno": None,
                        "in_app": True,
                    }
                ],
            },
            "request": {"headers": {"Authorization": "Bearer abc123"}},
            "breadcrumbs": [],
            "tags": {"api_token": "secret-value"},
        }
    )


def shared_stream_message(event: EventV1, message_id: str) -> tuple[str, dict[str, str]]:
    project_id, organization_id = uuid4(), uuid4()
    return (
        message_id,
        {
            "project_id": str(project_id),
            "organization_id": str(organization_id),
            "event_id": str(event.event_id),
            "payload": event.model_dump_json(),
        },
    )


@pytest.mark.asyncio
async def test_commit_precedes_ack_and_replay_does_not_double_count() -> None:
    pool, redis, settings = FakePool(), FakeRedis(), Settings()
    event = make_event()
    message = shared_stream_message(event, "1-0")

    await process_messages(redis, pool, settings, [message])
    await process_messages(redis, pool, settings, [message])

    assert len(pool.connection.raw_ids) == 1
    assert sum(pool.connection.issue_counts.values()) == 1
    assert len(pool.connection.event_rows) == 1
    assert redis.acked == ["1-0", "1-0"]


@pytest.mark.asyncio
async def test_cancelled_mid_batch_rolls_back_and_replay_loses_no_events() -> None:
    pool, redis, settings = FakePool(), FakeRedis(), Settings()
    messages = [
        shared_stream_message(make_event(), "1-0"),
        shared_stream_message(make_event(), "2-0"),
    ]
    messages[1][1]["project_id"] = messages[0][1]["project_id"]
    messages[1][1]["organization_id"] = messages[0][1]["organization_id"]
    pool.connection.crash_after = 1

    task = asyncio.create_task(process_messages(redis, pool, settings, messages))
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pool.connection.raw_ids == set()
    assert pool.connection.issue_counts == {}
    assert pool.connection.event_rows == set()
    assert redis.acked == []

    pool.connection.crash_after = None
    await process_messages(redis, pool, settings, messages)

    assert len(pool.connection.raw_ids) == 2
    assert list(pool.connection.issue_counts.values()) == [2]
    assert len(pool.connection.event_rows) == 2
    assert set(redis.acked) == {"1-0", "2-0"}


@pytest.mark.asyncio
async def test_poison_message_moves_to_dead_letter_after_max_attempts() -> None:
    redis = FakeRedis()
    settings = Settings(max_attempts=2, retry_base_ms=1, retry_max_ms=1)
    message = (
        "9-0",
        {
            "project_id": str(uuid4()),
            "organization_id": str(uuid4()),
            "event_id": str(uuid4()),
            "payload": "{not-json",
        },
    )

    await process_messages(redis, FakePool(), settings, [message])
    await process_messages(redis, FakePool(), settings, [message])

    assert redis.acked == ["9-0"]
    assert redis.dead_letters[0]["source_id"] == "9-0"
    assert redis.dead_letters[0]["failure_attempts"] == "2"
    assert "failure_reason" in redis.dead_letters[0]


def test_normalization_scrubs_secrets_and_preserves_stack_frames() -> None:
    normalized = normalize_event(make_event())
    assert normalized.request["headers"]["Authorization"] == "[Filtered]"
    assert normalized.tags["api_token"] == "[Filtered]"
    assert normalized.exception.stacktrace[0].filename == "app/main.py"
    assert normalized.exception.stacktrace[0].function == "handle_request"
    assert generate_fingerprint(normalized)



def test_dead_letter_payload_scrubbing_handles_valid_json() -> None:
    scrubbed = scrub_raw_payload(
        '{"request":{"headers":{"Authorization":"Bearer abc123"}},'
        '"api_token":"top-secret"}'
    )
    assert "abc123" not in scrubbed
    assert "top-secret" not in scrubbed
    assert "[Filtered]" in scrubbed
