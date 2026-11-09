import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, get_pg_pool, get_redis_client

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_KEY = "test-public-project-key"


class FakePool:
    def __init__(self) -> None:
        self.valid_key_hash = hashlib.sha256(PROJECT_KEY.encode()).hexdigest()

    async def close(self) -> None:
        return None

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        assert "project_keys" in query
        project_id, key_hash = args
        if project_id == PROJECT_ID and key_hash == self.valid_key_hash:
            return {
                "id": PROJECT_ID,
                "organization_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            }
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self.fail = False

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        if self.fail:
            from redis.exceptions import RedisError

            raise RedisError("Redis unavailable")
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"
            self.issue_users[issue_key] = set()
        else:
            issue = self.issues[issue_key]
            issue["event_count"] += 1
            issue["first_seen"] = min(issue["first_seen"], occurred_at)
            issue["last_seen"] = max(issue["last_seen"], occurred_at)
            if release is not None:
                issue["last_release"] = release
            if (
                issue["status"] == "resolved"
                and release is not None
                and release != issue.get("resolved_in_release")
            ):
                issue["status"] = "regressed"

        if user_id is not None and user_id not in self.issue_users[issue_key]:
            self.issue_users[issue_key].add(user_id)
            self.issues[issue_key]["user_count"] += 1

        return {"inserted_event_id": event_id, "issue_id": self.issues[issue_key]["id"]}

    async def fetchval(
        self,
        query: str,
        project_id: UUID,
        organization_id: UUID,
        event_id: UUID,
        payload_json: str,
    ) -> UUID | None:
        raise AssertionError("raw event persistence must use the atomic issue-upsert statement")


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakePool, FakeRedis]]:
    pool = FakePool()
    redis = FakeRedis()

    async def create_pool(**kwargs: Any) -> FakePool:
        return pool

    monkeypatch.setattr("app.main.asyncpg.create_pool", create_pool)
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql://localhost/oberyn_test",
        )
    )
    app.dependency_overrides[get_pg_pool] = lambda: pool
    app.dependency_overrides[get_redis_client] = lambda: cast(Any, redis)
    with TestClient(app) as client:
        yield client, pool, redis


def valid_event() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURES / "event-v1.valid.json").read_text()),
    )


def headers(project_id: UUID = PROJECT_ID, key: str = PROJECT_KEY) -> dict[str, str]:
    return {
        "X-Oberyn-Project-Id": str(project_id),
        "X-Oberyn-Key": key,
        "Content-Type": "application/json",
    }


def test_valid_event_is_accepted_and_enqueued(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    payload = valid_event()

    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "event_id": payload["event_id"]}
    assert len(redis.entries) == 1
    stream, fields = redis.entries[0]
    assert stream == "events.raw"
    assert fields["project_id"] == str(PROJECT_ID)
    assert fields["event_id"] == payload["event_id"]
    assert fields["organization_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    enqueued_payload = json.loads(fields["payload"])
    assert enqueued_payload["event_id"] == payload["event_id"]
    assert enqueued_payload["exception"] == payload["exception"]


def test_invalid_event_payload_returns_422(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    payload = valid_event()
    payload["level"] = "critical"

    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert response.status_code == 422
    assert redis.entries == []


def test_invalid_json_returns_422(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=b'{"schema_version":',
    )
    assert response.status_code == 422
    assert redis.entries == []


def test_invalid_project_key_returns_401(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(key="incorrect-key"),
        json=valid_event(),
    )
    assert response.status_code == 401
    assert redis.entries == []


def test_unknown_project_returns_401(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    unknown_project = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    response = client.post(
        f"/api/{unknown_project}/envelope",
        headers=headers(project_id=unknown_project),
        json=valid_event(),
    )
    assert response.status_code == 401
    assert redis.entries == []


def test_project_header_mismatch_returns_401(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(project_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")),
        json=valid_event(),
    )
    assert response.status_code == 401
    assert redis.entries == []


def test_repeated_event_is_enqueued_and_worker_deduplicates(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    payload = valid_event()

    first = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)
    second = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert first.status_code == second.status_code == 202
    assert len(redis.entries) == 2
    assert redis.entries[0][1]["event_id"] == redis.entries[1][1]["event_id"]


def test_oversized_content_length_returns_413(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers={**headers(), "Content-Length": str(201 * 1024)},
        content=b"x",
    )
    assert response.status_code == 413
    assert redis.entries == []


def test_oversized_stream_without_content_length_returns_413(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    payload = b"x" * (200 * 1024 + 1)
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=iter([payload]),
    )
    assert response.status_code == 413
    assert redis.entries == []


def test_missing_project_key_returns_401(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers={"Content-Type": "application/json"},
        json=valid_event(),
    )
    assert response.status_code == 401
    assert redis.entries == []


def test_redis_enqueue_failure_returns_503(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.fail = True

    response = client.post(
        f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event()
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Ingestion queue unavailable"}
    assert redis.entries == []
