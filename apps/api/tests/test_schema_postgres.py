from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import asyncpg
import pytest

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def pg_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(pg_url())
    try:
        yield connection
    finally:
        await connection.close()


async def test_tenant_relationships_and_issue_fingerprint(conn: asyncpg.Connection) -> None:
    org1, org2, member1, member2, project1, project2 = [uuid.uuid4() for _ in range(6)]
    await conn.execute(
        "INSERT INTO organizations(id,name,slug) VALUES($1,'A',$2),($3,'B',$4)",
        org1, f"a-{org1}", org2, f"b-{org2}",
    )
    await conn.execute(
        "INSERT INTO members(id,organization_id,email,role) "
        "VALUES($1,$2,$3,'owner'),($4,$5,$6,'owner')",
        member1, org1, f"{member1}@example.test",
        member2, org2, f"{member2}@example.test",
    )
    await conn.execute("UPDATE organizations SET owner_member_id=$1 WHERE id=$2", member1, org1)
    await conn.execute("UPDATE organizations SET owner_member_id=$1 WHERE id=$2", member2, org2)
    await conn.execute(
        "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) "
        "VALUES($1,$2,$3,'A','a'),($4,$5,$6,'B','b')",
        project1, org1, member1, project2, org2, member2,
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(
            "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) "
            "VALUES($1,$2,$3,'bad','bad')",
            uuid.uuid4(), org1, member2,
        )

    now = datetime.now(timezone.utc)
    issue_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO issues(id,project_id,organization_id,fingerprint,title,first_seen,last_seen) "
        "VALUES($1,$2,$3,'fp','boom',$4,$4)",
        issue_id, project1, org1, now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO issues(id,project_id,organization_id,fingerprint,title,first_seen,last_seen) "
            "VALUES($1,$2,$3,'fp','duplicate',$4,$4)",
            uuid.uuid4(), project1, org1, now,
        )


async def test_event_partition_jsonb_columns_and_indexes(conn: asyncpg.Connection) -> None:
    org, member, project, issue = [uuid.uuid4() for _ in range(4)]
    await conn.execute(
        "INSERT INTO organizations(id,name,slug) VALUES($1,'events',$2)", org, f"events-{org}"
    )
    await conn.execute(
        "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
        member, org, f"{member}@example.test",
    )
    await conn.execute("UPDATE organizations SET owner_member_id=$1 WHERE id=$2", member, org)
    await conn.execute(
        "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) "
        "VALUES($1,$2,$3,'events','events')",
        project, org, member,
    )
    now = datetime.now(timezone.utc)
    await conn.execute(
        "INSERT INTO issues(id,project_id,organization_id,fingerprint,title,first_seen,last_seen) "
        "VALUES($1,$2,$3,'event-fp','event',$4,$4)",
        issue, project, org, now,
    )
    event_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO events(id,issue_id,project_id,organization_id,received_at,release,environment,payload) "
        "VALUES($1,$2,$3,$4,$5,'1.2.3','test',$6::jsonb)",
        event_id, issue, project, org, now, '{"message":"boom"}',
    )
    row = await conn.fetchrow(
        "SELECT release, environment, payload->>'message' AS message FROM events WHERE id=$1",
        event_id,
    )
    assert row is not None
    assert row["release"] == "1.2.3"
    assert row["environment"] == "test"
    assert row["message"] == "boom"

    indexes = await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename='events'")
    names = {item["indexname"] for item in indexes}
    assert "ix_events_issue_received_at" in names
    assert "ix_events_project_received_at" in names


async def test_invalid_issue_status_is_rejected(conn: asyncpg.Connection) -> None:
    org, member, project = [uuid.uuid4() for _ in range(3)]
    await conn.execute(
        "INSERT INTO organizations(id,name,slug) VALUES($1,'status',$2)", org, f"status-{org}"
    )
    await conn.execute(
        "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
        member, org, f"{member}@example.test",
    )
    await conn.execute(
        "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) "
        "VALUES($1,$2,$3,'status','status')",
        project, org, member,
    )
    now = datetime.now(timezone.utc)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO issues(id,project_id,organization_id,fingerprint,title,status,first_seen,last_seen) "
            "VALUES($1,$2,$3,'bad','bad','closed',$4,$4)",
            uuid.uuid4(), project, org, now,
        )


async def test_raw_event_idempotency_is_scoped_to_project(
    conn: asyncpg.Connection,
) -> None:
    org, member, project = [uuid.uuid4() for _ in range(3)]
    await conn.execute(
        "INSERT INTO organizations(id,name,slug) VALUES($1,'raw-events',$2)",
        org,
        f"raw-events-{org}",
    )
    await conn.execute(
        "INSERT INTO members(id,organization_id,email,role) VALUES($1,$2,$3,'owner')",
        member,
        org,
        f"{member}@example.test",
    )
    await conn.execute(
        "INSERT INTO projects(id,organization_id,created_by_member_id,name,slug) "
        "VALUES($1,$2,$3,'raw-events','raw-events')",
        project,
        org,
        member,
    )

    event_id = uuid.uuid4()
    query = (
        "INSERT INTO raw_events(project_id,organization_id,event_id,payload) "
        "VALUES($1,$2,$3,$4::jsonb) "
        "ON CONFLICT (project_id,event_id) DO NOTHING RETURNING event_id"
    )
    first = await conn.fetchval(query, project, org, event_id, '{"level":"error"}')
    duplicate = await conn.fetchval(query, project, org, event_id, '{"level":"error"}')

    assert first == event_id
    assert duplicate is None
    assert await conn.fetchval(
        "SELECT count(*) FROM raw_events WHERE project_id=$1 AND event_id=$2",
        project,
        event_id,
    ) == 1
