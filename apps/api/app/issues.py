"""Single-statement event persistence and concurrency-safe issue upserts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

UPSERT_EVENT_SQL = """
WITH inserted AS (
    INSERT INTO raw_events (project_id, organization_id, event_id, payload)
    VALUES ($1, $2, $3, $4::jsonb)
    ON CONFLICT (project_id, event_id) DO NOTHING
    RETURNING project_id, event_id
),
release_upsert AS (
    INSERT INTO releases (id, project_id, organization_id, version)
    SELECT gen_random_uuid(), $1, $2, $9
    WHERE EXISTS (SELECT 1 FROM inserted) AND $9::text IS NOT NULL
    ON CONFLICT (project_id, version) DO UPDATE
    SET version = EXCLUDED.version
    RETURNING version, created_at
),
issue AS (
    INSERT INTO issues (
        id, project_id, organization_id, fingerprint, title,
        first_seen, last_seen, event_count, user_count, status,
        first_release, last_release
    )
    SELECT gen_random_uuid(), $1, $2, $5, $6, $7, $7, 1, 0, 'unresolved', $9, $9
    WHERE EXISTS (SELECT 1 FROM inserted)
    ON CONFLICT (project_id, fingerprint) DO UPDATE
    SET first_seen = LEAST(issues.first_seen, EXCLUDED.first_seen),
        last_seen = GREATEST(issues.last_seen, EXCLUDED.last_seen),
        event_count = issues.event_count + 1,
        first_release = CASE
            WHEN EXCLUDED.first_seen < issues.first_seen THEN EXCLUDED.first_release
            ELSE COALESCE(issues.first_release, EXCLUDED.first_release)
        END,
        last_release = CASE
            WHEN EXCLUDED.last_seen > issues.last_seen THEN EXCLUDED.last_release
            ELSE COALESCE(issues.last_release, EXCLUDED.last_release)
        END,
        status = CASE
            WHEN issues.status = 'resolved'
             AND EXCLUDED.last_release IS NOT NULL
             AND EXCLUDED.last_release IS DISTINCT FROM issues.resolved_in_release
             AND EXCLUDED.last_seen > COALESCE(issues.resolved_at, '-infinity'::timestamptz)
            THEN 'regressed'
            ELSE issues.status
        END,
        resolved_at = CASE
            WHEN issues.status = 'resolved'
             AND EXCLUDED.last_release IS NOT NULL
             AND EXCLUDED.last_release IS DISTINCT FROM issues.resolved_in_release
             AND EXCLUDED.last_seen > COALESCE(issues.resolved_at, '-infinity'::timestamptz)
            THEN NULL
            ELSE issues.resolved_at
        END,
        resolved_in_release = CASE
            WHEN issues.status = 'resolved'
             AND EXCLUDED.last_release IS NOT NULL
             AND EXCLUDED.last_seen > issues.last_seen
             AND EXCLUDED.last_seen <= issues.resolved_at
            THEN EXCLUDED.last_release
            ELSE issues.resolved_in_release
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
    (SELECT id FROM issue) AS issue_id,
    (SELECT count(*) FROM new_user) AS new_user_inserted
"""


async def persist_event_and_upsert_issue(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    project_id: UUID,
    organization_id: UUID,
    event_id: UUID,
    payload_json: str,
    fingerprint: str,
    title: str,
    occurred_at: datetime,
    user_id: str | None,
    release: str | None = None,
) -> asyncpg.Record:
    """Persist the idempotent raw event and update its issue in one SQL statement."""
    if hasattr(pool, "acquire"):
        async with pool.acquire() as connection:
            async with connection.transaction():
                result = await connection.fetchrow(
                    UPSERT_EVENT_SQL,
                    project_id,
                    organization_id,
                    event_id,
                    payload_json,
                    fingerprint,
                    title,
                    occurred_at,
                    user_id,
                    release,
                )
                if result is not None and result["inserted_event_id"] is not None:
                    await connection.execute(
                        "UPDATE raw_events SET issue_id=$3 WHERE project_id=$1 AND event_id=$2",
                        project_id,
                        event_id,
                        result["issue_id"],
                    )
    else:
        result = await pool.fetchrow(
            UPSERT_EVENT_SQL,
            project_id,
            organization_id,
            event_id,
            payload_json,
            fingerprint,
            title,
            occurred_at,
            user_id,
            release,
        )
        if (
            result is not None
            and result["inserted_event_id"] is not None
            and hasattr(pool, "execute")
        ):
            await pool.execute(
                "UPDATE raw_events SET issue_id=$3 WHERE project_id=$1 AND event_id=$2",
                project_id,
                event_id,
                result["issue_id"],
            )
    if result is None:
        raise RuntimeError("atomic event upsert returned no result row")
    return result
