from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest
from fastapi import HTTPException

from app.dashboard import (
    IssueStatusInput,
    current_user,
    decode_cursor,
    encode_cursor,
    list_issues,
    membership,
    parse_tag_filter,
    require_admin,
    update_issue_status,
)


def test_cursor_round_trip() -> None:
    seen = datetime(2026, 11, 7, 1, 30, tzinfo=UTC)
    issue_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert decode_cursor(encode_cursor(seen, issue_id)) == (seen, issue_id)


def test_cursor_rejects_malformed_values() -> None:
    with pytest.raises(HTTPException) as error:
        decode_cursor("not-a-cursor")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_bearer_token_is_rejected() -> None:
    with pytest.raises(HTTPException) as error:
        await current_user(None)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_membership_query_is_scoped_to_org_and_clerk_subject() -> None:
    org_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    row = {"id": UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"), "role": "owner"}
    fake = SimpleNamespace(fetchrow=AsyncMock(return_value=row))
    result = await membership(
        cast(asyncpg.Pool, fake), org_id, {"id": "user_123", "email": "dev@example.com"}
    )
    assert result == row
    query, passed_org, passed_user = fake.fetchrow.await_args.args
    assert "m.organization_id=$1 AND m.clerk_user_id=$2" in query
    assert passed_org == org_id
    assert passed_user == "user_123"


@pytest.mark.asyncio
async def test_non_member_cannot_access_organization() -> None:
    fake = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await membership(
            cast(asyncpg.Pool, fake),
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            {"id": "user_outside", "email": ""},
        )
    assert error.value.status_code == 404



def test_tag_filter_parses_multiple_exact_values() -> None:
    assert parse_tag_filter("browser=chrome,region=ap-south") == {
        "browser": "chrome",
        "region": "ap-south",
    }


def test_tag_filter_rejects_malformed_pairs() -> None:
    with pytest.raises(HTTPException) as error:
        parse_tag_filter("browser,region=ap-south")
    assert error.value.status_code == 422


def test_member_cannot_perform_issue_mutations() -> None:
    with pytest.raises(HTTPException) as error:
        require_admin({"role": "member"}, "merge issues")  # type: ignore[arg-type]
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_issue_filters_are_parameterized_and_combined() -> None:
    org_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    project_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    fake = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"id": UUID(int=1), "role": "member"}),
        fetchval=AsyncMock(return_value=True),
        fetch=AsyncMock(return_value=[]),
    )
    await list_issues(
        org_id=org_id,
        project_id=project_id,
        status="resolved",
        release="1.2.3",
        environment="production",
        level="error",
        tags="browser=chrome",
        search="TypeError",
        cursor=None,
        limit=20,
        user={"id": "user_123", "email": ""},
        db=cast(asyncpg.Pool, fake),
    )
    query, *args = fake.fetch.await_args.args
    assert "issues.status=$2" in query
    assert "re.payload->>'release'=$3" in query
    assert "re.payload->>'environment'=$4" in query
    assert "re.payload->>'level'=$5" in query
    assert "re.payload->'tags' @> $6::jsonb" in query
    assert "issues.title ILIKE $7" in query
    assert "ORDER BY issues.last_seen DESC,issues.id DESC" in query
    assert args[:6] == [project_id, "resolved", "1.2.3", "production", "error", '{"browser": "chrome"}']
    assert args[6] == "%TypeError%"
    assert args[7] == 21


@pytest.mark.asyncio
async def test_admin_can_transition_issue_to_resolved() -> None:
    org_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    project_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    issue_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    updated = {"id": issue_id, "status": "resolved", "resolved_in_release": "1.2.3"}
    fake = SimpleNamespace(
        fetchrow=AsyncMock(
            side_effect=[
                {"id": UUID(int=1), "role": "admin"},
                updated,
            ]
        )
    )
    result = await update_issue_status(
        org_id,
        project_id,
        issue_id,
        IssueStatusInput(status="resolved"),
        {"id": "user_admin", "email": ""},
        cast(asyncpg.Pool, fake),
    )
    assert result["issue"]["status"] == "resolved"
    update_query = fake.fetchrow.await_args_list[1].args[0]
    assert "resolved_at=CASE WHEN $4='resolved' THEN now()" in update_query
    assert "resolved_in_release=CASE WHEN $4='resolved' THEN last_release" in update_query


@pytest.mark.asyncio
async def test_member_cannot_change_issue_status() -> None:
    fake = SimpleNamespace(fetchrow=AsyncMock(return_value={"id": UUID(int=1), "role": "member"}))
    with pytest.raises(HTTPException) as error:
        await update_issue_status(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            IssueStatusInput(status="ignored"),
            {"id": "user_member", "email": ""},
            cast(asyncpg.Pool, fake),
        )
    assert error.value.status_code == 403
    assert fake.fetchrow.await_count == 1
