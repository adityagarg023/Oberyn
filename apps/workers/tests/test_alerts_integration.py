from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from workers.alerts import deliver_pending, evaluate_rules

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set TEST_DATABASE_URL for alerting integration tests",
)


def database_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


class MockNotifier:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []
        self.fail_first = fail_first

    async def send(self, payload: dict[str, Any], destination: str) -> None:
        self.calls.append((payload, destination))
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("simulated provider outage")


async def create_alert_fixture(
    pool: asyncpg.Pool,
    rule_type: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    organization_id, member_id, project_id, issue_id, rule_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    now = datetime.now(UTC)
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,slug) VALUES($1,'alert-test',$2)",
            organization_id,
            f"alert-test-{organization_id}",
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
            VALUES($1,$2,$3,'alert test','alert-test')
            """,
            project_id,
            organization_id,
            member_id,
        )
        await connection.execute(
            """
            INSERT INTO issues(
                id,project_id,organization_id,fingerprint,title,status,
                first_seen,last_seen,event_count,user_count
            )
            VALUES($1,$2,$3,$4,'RuntimeError: alert fixture',$5,$6,$6,2,2)
            """,
            issue_id,
            project_id,
            organization_id,
            str(issue_id),
            "regressed" if rule_type == "regression" else "unresolved",
            now,
        )
        await connection.execute(
            """
            INSERT INTO alert_rules(
                id,project_id,organization_id,name,threshold,window_seconds,config
            )
            VALUES($1,$2,$3,$4,2,300,$5::jsonb)
            """,
            rule_id,
            project_id,
            organization_id,
            f"{rule_type} rule",
            __import__("json").dumps(
                {
                    "type": rule_type,
                    "cooldown_seconds": 300,
                    "channels": ["email"],
                    "email_to": ["oncall@example.test"],
                }
            ),
        )
        for index in range(2):
            await connection.execute(
                """
                INSERT INTO raw_events(project_id,organization_id,event_id,issue_id,payload)
                VALUES($1,$2,$3,$4,'{}'::jsonb)
                """,
                project_id,
                organization_id,
                uuid.uuid4(),
                issue_id,
            )
            await connection.execute(
                "INSERT INTO issue_users(issue_id,project_id,user_id) VALUES($1,$2,$3)",
                issue_id,
                project_id,
                f"user-{index}",
            )
    return organization_id, project_id, issue_id, rule_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rule_type", "is_new_issue", "is_regression"),
    [
        ("new_issue", True, False),
        ("regression", False, True),
        ("event_count", False, False),
        ("user_count", False, False),
    ],
)
async def test_alert_rules_enqueue_once_and_deliver_through_mock_provider(
    rule_type: str,
    is_new_issue: bool,
    is_regression: bool,
) -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=4)
    organization_id, project_id, issue_id, _rule_id = await create_alert_fixture(
        pool, rule_type
    )
    notifier = MockNotifier()
    factory = lambda _payload: [(notifier, "oncall@example.test")]
    try:
        async with pool.acquire() as connection:
            queued = await evaluate_rules(
                connection,
                project_id=project_id,
                issue_id=issue_id,
                occurred_at=datetime.now(UTC),
                is_new_issue=is_new_issue,
                is_regression=is_regression,
            )
            duplicate = await evaluate_rules(
                connection,
                project_id=project_id,
                issue_id=issue_id,
                occurred_at=datetime.now(UTC),
                is_new_issue=is_new_issue,
                is_regression=is_regression,
            )
        assert queued == 1
        assert duplicate == 0

        assert await deliver_pending(pool, notifier_factory=factory) == 1
        assert len(notifier.calls) == 1
        async with pool.acquire() as connection:
            history = await connection.fetchrow(
                "SELECT status,attempts,delivered_at FROM alert_history "
                "WHERE project_id=$1 AND issue_id=$2",
                project_id,
                issue_id,
            )
        assert history is not None
        assert history["status"] == "delivered"
        assert history["attempts"] == 1
        assert history["delivered_at"] is not None
    finally:
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM organizations WHERE id=$1", organization_id)
        await pool.close()


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_and_history_is_updated() -> None:
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=4)
    organization_id, project_id, issue_id, _rule_id = await create_alert_fixture(
        pool, "new_issue"
    )
    notifier = MockNotifier(fail_first=True)
    factory = lambda _payload: [(notifier, "oncall@example.test")]
    try:
        async with pool.acquire() as connection:
            assert await evaluate_rules(
                connection,
                project_id=project_id,
                issue_id=issue_id,
                occurred_at=datetime.now(UTC),
                is_new_issue=True,
                is_regression=False,
            ) == 1

        assert await deliver_pending(pool, notifier_factory=factory) == 0
        async with pool.acquire() as connection:
            await connection.execute(
                "UPDATE alert_history SET next_attempt_at=now() WHERE project_id=$1",
                project_id,
            )
        assert await deliver_pending(pool, notifier_factory=factory) == 1
        assert len(notifier.calls) == 2

        async with pool.acquire() as connection:
            history = await connection.fetchrow(
                "SELECT status,attempts,last_error,delivered_at FROM alert_history "
                "WHERE project_id=$1 AND issue_id=$2",
                project_id,
                issue_id,
            )
        assert history is not None
        assert history["status"] == "delivered"
        assert history["attempts"] == 2
        assert history["last_error"] is None
        assert history["delivered_at"] is not None
    finally:
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM organizations WHERE id=$1", organization_id)
        await pool.close()
