from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from app.contracts.events import EventV1
from app.fingerprints import generate_fingerprint
from app.issues import persist_event_and_upsert_issue


async def persist_batch(
    pool: asyncpg.Pool,
    events: list[tuple[UUID, UUID, EventV1]],
) -> int:
    """Persist one batch atomically; return the number of newly inserted events.

    raw_events has the (project_id, event_id) uniqueness boundary. Its insert,
    issue counter upsert, issue link, and events row all share this transaction.
    A replay therefore cannot increment issue.event_count twice.
    """
    if not events:
        return 0

    inserted_count = 0
    async with pool.acquire() as connection:
        async with connection.transaction():
            for project_id, organization_id, event in events:
                result = await persist_event_and_upsert_issue(
                    connection,
                    project_id=project_id,
                    organization_id=organization_id,
                    event_id=event.event_id,
                    payload_json=event.model_dump_json(),
                    fingerprint=generate_fingerprint(event),
                    title=f"{event.exception.type}: {event.exception.value}",
                    occurred_at=event.timestamp,
                    user_id=event.user.id if event.user is not None else None,
                    release=event.release,
                )
                if result["inserted_event_id"] is None:
                    continue

                issue_id = result["issue_id"]
                await connection.execute(
                    """
                    INSERT INTO events (
                        id, issue_id, project_id, organization_id, received_at,
                        release, environment, payload
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    event.event_id,
                    issue_id,
                    project_id,
                    organization_id,
                    datetime.now(UTC),
                    event.release,
                    event.environment or "production",
                    event.model_dump_json(),
                )
                inserted_count += 1

    return inserted_count
