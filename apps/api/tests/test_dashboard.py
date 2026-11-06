from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import asyncpg
import pytest
from fastapi import HTTPException

from app.dashboard import current_user, decode_cursor, encode_cursor, membership


def test_cursor_round_trip() -> None:
    seen = datetime(2026, 11, 7, 1, 30, tzinfo=timezone.utc)
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
