"""Atomic PostgreSQL issue upserts for processed events."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg


async def upsert_issue(
    connection: asyncpg.Connection,
    *,
    project_id: UUID,
    organization_id: UUID,
    fingerprint: str,
    title: str,
    occurred_at: datetime,
    user_id: str | None = None,
) -> UUID:
    """Create or update one issue and its distinct-user count atomically.

    Callers may pass any acquired asyncpg connection. The transaction keeps the
    issue row lock while the distinct-user row and counter are updated.
    """
    if not fingerprint or len(fingerprint) > 128:
        raise ValueError("fingerprint must contain 1-128 characters")
    async with connection.transaction():
        issue_id = await connection.fetchval(
            """
            INSERT INTO issues (
                id, project_id, organization_id, fingerprint, title,
                first_seen, last_seen, event_count, user_count, status
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $5, 1, 0, 'unresolved')
            ON CONFLICT (project_id, fingerprint) DO UPDATE
            SET last_seen = GREATEST(issues.last_seen, EXCLUDED.last_seen),
                event_count = issues.event_count + 1,
                status = CASE
                    WHEN issues.status = 'resolved' THEN 'regressed'
                    ELSE issues.status
                END
            RETURNING id
            """,
            project_id,
            organization_id,
            fingerprint,
            title,
            occurred_at,
        )
        if user_id is not None:
            inserted_user = await connection.fetchval(
                """
                INSERT INTO issue_users (issue_id, project_id, user_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (issue_id, user_id) DO NOTHING
                RETURNING user_id
                """,
                issue_id,
                project_id,
                user_id,
            )
            if inserted_user is not None:
                await connection.execute(
                    """
                    UPDATE issues
                    SET user_count = user_count + 1
                    WHERE id = $1 AND project_id = $2
                    """,
                    issue_id,
                    project_id,
                )
        return issue_id
