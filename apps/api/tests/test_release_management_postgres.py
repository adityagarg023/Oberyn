from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.issues import persist_event_and_upsert_issue

DATABASE_URL = __import__("os").getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set TEST_DATABASE_URL for PostgreSQL release-management tests",
)


def database_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


async def create_project(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id, member_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,slug) VALUES($1,'release-test',$2)",
            organization_id,
            f"release-test-{organization_id}",
        )
        await connection.execute(
            "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
            member_id,
            organization_id,
            f"{member_id}@example.test",
        )
        await connection.execute(
            "UPDATE organizations SET owner_member_id=$1 WHERE id=$2",
            member_id,
            organization_id,
        )
        await connection.execute(
            """
            INSERT INTO projects(id,organization_id,created_by_member_id,name,slug)
            VALUES($1,$2,$3,'release test','release-test')
            """,
            project_id,
            organization_id,
            member_id,
        )
    return organization_id, project_id


async def write_event(
    connection: asyncpg.Connection,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    release: str,
    occurred_at: datetime,
    user_id: str,
    event_id: uuid.UUID | None = None,
) -> asyncpg.Record:
    event_id = event_id or uuid.uuid4()
    payload = {
        "event_id": str(event_id),
        "release": release,
        "environment": "production",
    }
    return await persist_event_and_upsert_issue(
        connection,
        project_id=project_id,
        organization_id=organization_id,
        event_id=event_id,
        payload_json=json.dumps(payload),
        fingerprint="release-order-fingerprint",
        title="RuntimeError: release failure",
        occurred_at=occurred_at,
        user_id=user_id,
        release=release,
    )


@pytest.mark.asyncio
async def test_release_bounds_follow_release_order_and_duplicate_events_are_idempotent() -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=4)
    organization_id, project_id = await create_project(pool)
    now = datetime.now(UTC)
    try:
        async with pool.acquire() as connection:
            await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="2.0.0",
                occurred_at=now,
                user_id="user-a",
            )
            second_event_id = uuid.uuid4()
            await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="2.1.0",
                occurred_at=now - timedelta(days=1),
                user_id="user-b",
                event_id=second_event_id,
            )
            duplicate = await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="2.1.0",
                occurred_at=now - timedelta(days=1),
                user_id="user-c",
                event_id=second_event_id,
            )
            issue = await connection.fetchrow(
                """
                SELECT first_seen,last_seen,event_count,user_count,first_release,last_release
                FROM issues WHERE project_id=$1 AND fingerprint=$2
                """,
                project_id,
                "release-order-fingerprint",
            )
            raw_count = await connection.fetchval(
                "SELECT count(*) FROM raw_events WHERE project_id=$1",
                project_id,
            )

        assert duplicate["inserted_event_id"] is None
        assert issue is not None
        assert issue["first_release"] == "2.0.0"
        assert issue["last_release"] == "2.1.0"
        assert issue["first_seen"] == now - timedelta(days=1)
        assert issue["last_seen"] == now
        assert issue["event_count"] == 2
        assert issue["user_count"] == 2
        assert raw_count == 2
    finally:
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM organizations WHERE id=$1", organization_id)
        await pool.close()


@pytest.mark.asyncio
async def test_issue_regresses_only_when_a_newer_release_is_affected() -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=4)
    organization_id, project_id = await create_project(pool)
    now = datetime.now(UTC)
    try:
        async with pool.acquire() as connection:
            await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="3.0.0",
                occurred_at=now,
                user_id="user-a",
            )
            issue_id = await connection.fetchval(
                "SELECT id FROM issues WHERE project_id=$1 AND fingerprint=$2",
                project_id,
                "release-order-fingerprint",
            )
            await connection.execute(
                """
                UPDATE issues
                SET status='resolved', resolved_at=now(), resolved_in_release='3.0.0'
                WHERE id=$1
                """,
                issue_id,
            )

            await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="3.0.0",
                occurred_at=now + timedelta(seconds=1),
                user_id="user-a",
            )
            same_release_status = await connection.fetchval(
                "SELECT status FROM issues WHERE id=$1",
                issue_id,
            )
            await write_event(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                release="3.1.0",
                occurred_at=now - timedelta(days=2),
                user_id="user-b",
            )
            issue = await connection.fetchrow(
                "SELECT status,resolved_at,resolved_in_release,event_count,user_count,"
                "first_release,last_release FROM issues WHERE id=$1",
                issue_id,
            )

        assert same_release_status == "resolved"
        assert issue is not None
        assert issue["status"] == "regressed"
        assert issue["resolved_at"] is None
        assert issue["resolved_in_release"] == "3.0.0"
        assert issue["event_count"] == 3
        assert issue["user_count"] == 2
        assert issue["first_release"] == "3.0.0"
        assert issue["last_release"] == "3.1.0"
    finally:
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM organizations WHERE id=$1", organization_id)
        await pool.close()
