from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.issues import persist_event_and_upsert_issue

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set TEST_DATABASE_URL for PostgreSQL concurrency tests",
)


def database_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


async def create_project(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    organization_id, member_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,slug) VALUES($1,'issue-upsert-test',$2)",
            organization_id,
            f"issue-upsert-{organization_id}",
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
            VALUES($1,$2,$3,'test','test')
            """,
            project_id,
            organization_id,
            member_id,
        )
    return organization_id, project_id


@pytest.mark.asyncio
async def test_concurrent_events_create_one_issue_and_count_distinct_users() -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=16)
    try:
        organization_id, project_id = await create_project(pool)
        base_time = datetime.now(UTC)

        async def write(index: int) -> None:
            event_id = uuid.uuid4()
            payload = {"event_id": str(event_id), "exception": {"type": "RuntimeError"}}
            async with pool.acquire() as connection:
                result = await persist_event_and_upsert_issue(
                    connection,
                    project_id=project_id,
                    organization_id=organization_id,
                    event_id=event_id,
                    payload_json=json.dumps(payload),
                    fingerprint="same-fingerprint",
                    title="RuntimeError: failed",
                    occurred_at=base_time + timedelta(seconds=index),
                    user_id=f"user-{index % 4}",
                    release="1.0.0",
                )
            assert result["inserted_event_id"] == event_id
            assert result["issue_id"] is not None

        await asyncio.gather(*(write(index) for index in range(40)))

        async with pool.acquire() as connection:
            issues = await connection.fetch(
                """
                SELECT id, first_seen, last_seen, event_count, user_count, status
                FROM issues WHERE project_id=$1 AND fingerprint=$2
                """,
                project_id,
                "same-fingerprint",
            )
            raw_event_count = await connection.fetchval(
                "SELECT count(*) FROM raw_events WHERE project_id=$1",
                project_id,
            )
            issue_user_count = await connection.fetchval(
                "SELECT count(*) FROM issue_users WHERE project_id=$1",
                project_id,
            )

        assert len(issues) == 1
        assert issues[0]["event_count"] == 40
        assert issues[0]["user_count"] == 4
        assert issue_user_count == 4
        assert raw_event_count == 40
        assert issues[0]["first_seen"] == base_time
        assert issues[0]["last_seen"] == base_time + timedelta(seconds=39)
        assert issues[0]["status"] == "unresolved"

        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE issues SET status='resolved', resolved_at=now(), "
                "resolved_in_release=last_release WHERE id=$1",
                issues[0]["id"],
            )
            next_event_id = uuid.uuid4()
            await persist_event_and_upsert_issue(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                event_id=next_event_id,
                payload_json=json.dumps({"event_id": str(next_event_id)}),
                fingerprint="same-fingerprint",
                title="RuntimeError: failed",
                occurred_at=base_time + timedelta(seconds=40),
                user_id="user-0",
                release="1.0.0",
            )
            regressed = await connection.fetchrow(
                """
                SELECT first_seen, last_seen, event_count, user_count, status
                FROM issues WHERE project_id=$1 AND fingerprint=$2
                """,
                project_id,
                "same-fingerprint",
            )
        assert regressed is not None
        assert regressed["status"] == "resolved"

        async with pool.acquire() as connection:
            next_event_id = uuid.uuid4()
            await persist_event_and_upsert_issue(
                connection,
                project_id=project_id,
                organization_id=organization_id,
                event_id=next_event_id,
                payload_json=json.dumps({"event_id": str(next_event_id)}),
                fingerprint="same-fingerprint",
                title="RuntimeError: failed",
                occurred_at=base_time + timedelta(seconds=41),
                user_id="user-0",
                release="1.1.0",
            )
            regressed = await connection.fetchrow(
                "SELECT first_seen,last_seen,event_count,user_count,status,first_release,last_release FROM issues "
                "WHERE project_id=$1 AND fingerprint=$2", project_id, "same-fingerprint"
            )
        assert regressed is not None
        assert regressed["first_seen"] == base_time
        assert regressed["last_seen"] == base_time + timedelta(seconds=41)
        assert regressed["event_count"] == 42
        assert regressed["user_count"] == 4
        assert regressed["status"] == "regressed"
        assert regressed["first_release"] == "1.0.0"
        assert regressed["last_release"] == "1.1.0"
    finally:
        await pool.close()
