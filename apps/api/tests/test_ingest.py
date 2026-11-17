import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.config import Settings
from app.main import create_app, get_pg_pool, get_redis_client

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_KEY = "test-public-project-key"
ORGANIZATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class FakePool:
    def __init__(self) -> None:
        self.valid_key_hash = hashlib.sha256(PROJECT_KEY.encode()).hexdigest()
        self.cors_origins: list[str] = []

    async def close(self) -> None:
        return None

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        assert "project_keys" in query
        project_id, key_hash = args
        if project_id == PROJECT_ID and key_hash == self.valid_key_hash:
            return {
                "id": PROJECT_ID,
                "organization_id": ORGANIZATION_ID,
                "cors_origins": self.cors_origins,
                "monthly_event_quota": 1000,
            }
        return None

    async def fetchval(self, query: str, project_id: UUID) -> list[str]:
        assert "cors_origins" in query
        return self.cors_origins


class FakeRedis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.fail = False
        self.bucket_reject = False
        self.spike_reject = False
        self.quota_reject = False
        self.queue_reject = False
        self.dashboard_reject = False

    async def aclose(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RedisError("Redis unavailable")
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        if self.fail:
            raise RedisError("Redis unavailable")
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        if self.fail:
            raise RedisError("Redis unavailable")
        return int(self.values.pop(key, None) is not None)

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[Any]:
        if self.fail:
            raise RedisError("Redis unavailable")
        keys = args[:numkeys]
        values = args[numkeys:]
        if "HGET" in script:
            return [0, 3] if self.bucket_reject else [1, 0]
        if "redis.call('INCR'," in script:
            if str(keys[0]).startswith("oberyn:limit:dashboard:") and self.dashboard_reject:
                return [0, 4]
            return [0, 4] if self.spike_reject else [1, 0]
        if "XLEN" in script:
            stream, quota_key = str(keys[0]), str(keys[1])
            max_length, amount, _expires, monthly_limit = map(int, values[:4])
            if self.queue_reject:
                return [2, max_length]
            if self.quota_reject:
                return [3, 0]
            current_length = sum(1 for entry_stream, _ in self.entries if entry_stream == stream)
            if current_length >= max_length:
                return [2, current_length]
            used = self.counters.get(quota_key, 0) + amount
            if used > monthly_limit:
                return [3, used - amount]
            self.counters[quota_key] = used
            fields = {
                "project_id": str(values[4]),
                "organization_id": str(values[5]),
                "event_id": str(values[6]),
                "payload": str(values[7]),
            }
            self.entries.append((stream, fields))
            return [1, f"{len(self.entries)}-0"]
        raise AssertionError("Unexpected Redis Lua script")


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakePool, FakeRedis]]:
    pool = FakePool()
    redis = FakeRedis()

    async def create_pool(**kwargs: Any) -> FakePool:
        return pool

    monkeypatch.setattr("app.main.asyncpg.create_pool", create_pool)
    monkeypatch.setattr("app.main.Redis.from_url", lambda *args, **kwargs: redis)
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
    assert fields["organization_id"] == str(ORGANIZATION_ID)
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
    client, _pool, _redis = api_client
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=b'{"schema_version":',
    )
    assert response.status_code == 422


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
    other_project = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(project_id=other_project),
        json=valid_event(),
    )
    assert response.status_code == 401
    assert redis.entries == []


def test_repeated_event_is_enqueued_for_worker_deduplication(
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
    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=iter([b"x" * (200 * 1024 + 1)]),
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


def test_redis_unavailable_fails_closed_with_503(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.fail = True
    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event())
    assert response.status_code == 503
    assert response.json() == {"detail": "Ingestion authentication unavailable"}
    assert redis.entries == []


def test_project_rate_limit_returns_429_with_retry_after(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.bucket_reject = True
    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"


def test_spike_protection_returns_429(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.spike_reject = True
    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "4"


def test_monthly_quota_returns_429(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.quota_reject = True
    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event())
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_queue_backpressure_returns_503(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.queue_reject = True
    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=valid_event())
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


def test_project_cors_allowlist_handles_preflight(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, pool, _redis = api_client
    pool.cors_origins = ["https://console.example.com"]
    response = client.options(
        f"/api/{PROJECT_ID}/envelope",
        headers={
            "Origin": "https://console.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Oberyn-Key",
        },
    )
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "https://console.example.com"


def test_project_cors_allowlist_rejects_unlisted_origin(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, pool, _redis = api_client
    pool.cors_origins = ["https://console.example.com"]
    response = client.options(
        f"/api/{PROJECT_ID}/envelope",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 403


def test_dashboard_api_rate_limit_returns_429(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, _pool, redis = api_client
    redis.dashboard_reject = True
    response = client.get("/api/dashboard/organizations")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "4"
