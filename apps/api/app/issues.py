"""Single-statement event persistence and concurrency-safe issue upserts."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg


UPSERT_EVENT_SQL = """
WITH inserted AS (
    INSERT INTO raw_events (project_id, organization_id, event_id, payload)
    VALUES ($1, $2, $3, $4::jsonb)
    ON CONFLICT (project_id, event_id) DO NOTHING
    RETURNING project_id, event_id
),
issue AS (
    INSERT INTO issues (
        id, project_id, organization_id, fingerprint, title,
        first_seen, last_seen, event_count, user_count, status
    )
    SELECT gen_random_uuid(), $1, $2, $5, $6, $7, $7, 1, 0, 'unresolved'
    WHERE EXISTS (SELECT 1 FROM inserted)
    ON CONFLICT (project_id, fingerprint) DO UPDATE
    SET last_seen = GREATEST(issues.last_seen, EXCLUDED.last_seen),
        event_count = issues.event_count + 1,
        status = CASE
            WHEN issues.status = 'resolved' THEN 'regressed'
            ELSE issues.status
        END
    RETURNING id, project_id
),
new_user AS (
    INSERT INTO issue_users (issue_id, project_id, user_id)
    SELECT issue.id, issue.project_id, $8
    FROM issue
    WHERE $8::text IS NOT NULL
    ON CONFLICT (issue_id, user_id) DO NOTHING
    RETURNING issue_id
)
SELECT
    (SELECT event_id FROM inserted) AS inserted_event_id,
    (SELECT id FROM issue) AS issue_id
"""


async def persist_event_and_upsert_issue(
    pool: asyncpg.Pool,
    *,
    project_id: UUID,
    organization_id: UUID,
    event_id: UUID,
    payload_json: str,
    fingerprint: str,
    title: str,
    occurred_at: datetime,
    user_id: str | None,
) -> dict[str, Any]:
    """Persist the idempotent raw event and update its issue in one SQL statement."""
    return await pool.fetchrow(
        UPSERT_EVENT_SQL,
        project_id,
        organization_id,
        event_id,
        payload_json,
        fingerprint,
        title,
        occurred_at,
        user_id,
    )
