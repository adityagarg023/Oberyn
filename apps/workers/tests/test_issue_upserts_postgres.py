from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from workers.issues import upsert_issue

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Set TEST_DATABASE_URL for PostgreSQL concurrency tests")


def database_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


async def create_project(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id, member_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,slug) VALUES($1,'fingerprint-test',$2)",
            organization_id, f"fingerprint-{organization_id}",
        )
        await connection.execute(
            "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
            member_id, organization_id, f"{member_id}@example.test",
        )
        await connection.execute(
            "UPDATE organizations SET owner_member_id=$1 WHERE id=$2", member_id, organization_id
        )
        await connection.execute(
            "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) VALUES($1,$2,$3,'test','test')",
            project_id, organization_id, member_id,
        )
    return organization_id, project_id


@pytest.mark.asyncio
async def test_concurrent_upserts_create_one_issue_and_count_every_event() -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=12)
    try:
        organization_id, project_id = await create_project(pool)
        occurred_at = datetime.now(timezone.utc)

        async def write(index: int) -> uuid.UUID:
            async with pool.acquire() as connection:
                return await upsert_issue(
                    connection,
                    project_id=project_id,
                    organization_id=organization_id,
                    fingerprint="same-fingerprint",
                    title="RuntimeError: failed",
                    occurred_at=occurred_at,
                    user_id=f"user-{index % 4}",
                )

        issue_ids = await asyncio.gather(*(write(index) for index in range(40)))
        assert len(set(issue_ids)) == 1
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT event_count, user_count FROM issues WHERE project_id=$1 AND fingerprint=$2",
                project_id, "same-fingerprint",
            )
        assert row is not None
        assert row["event_count"] == 40
        assert row["user_count"] == 4
    finally:
        await pool.close()
