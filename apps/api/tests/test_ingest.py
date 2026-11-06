import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_KEY = "test-public-project-key"


class FakePool:
    def __init__(self) -> None:
        self.events: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        self.valid_key_hash = hashlib.sha256(PROJECT_KEY.encode()).hexdigest()
        self.closed = False

    async def fetchrow(self, query: str, project_id: UUID, key_hash: str) -> dict[str, UUID] | None:
        assert "project_keys" in query
        if project_id == PROJECT_ID and key_hash == self.valid_key_hash:
            return {"id": PROJECT_ID, "organization_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")}
        return None

    async def fetchval(
        self,
        query: str,
        project_id: UUID,
        organization_id: UUID,
        event_id: UUID,
        payload_json: str,
    ) -> UUID | None:
        assert "ON CONFLICT (project_id, event_id) DO NOTHING" in query
        key = (project_id, event_id)
        if key in self.events:
            return None
        self.events[key] = json.loads(payload_json)
        return event_id


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakePool]:
    pool = FakePool()

    async def create_pool(**kwargs: Any) -> FakePool:
        return pool

    async def close_pool() -> None:
        pool.closed = True

    monkeypatch.setattr("app.main.asyncpg.create_pool", create_pool)

    from app.main import get_pg_pool

    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql://localhost/oberyn_test",
        )
    )
    app.dependency_overrides[get_pg_pool] = lambda: pool
    client = TestClient(app)
    return client, pool


def valid_event() -> dict[str, Any]:
    return json.loads((FIXTURES / "event-v1.valid.json").read_text())


def headers(project_id: UUID = PROJECT_ID, key: str = PROJECT_KEY) -> dict[str, str]:
    return {
        "X-Oberyn-Project-Id": str(project_id),
        "X-Oberyn-Key": key,
        "Content-Type": "application/json",
    }


def test_valid_event_is_accepted_and_stored(api_client: tuple[TestClient, FakePool]) -> None:
    client, pool = api_client
    payload = valid_event()

    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "event_id": payload["event_id"],
        "duplicate": False,
    }
    assert len(pool.events) == 1


def test_invalid_event_payload_returns_422(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client
    payload = valid_event()
    payload["level"] = "critical"

    response = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert response.status_code == 422


def test_invalid_json_returns_422(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=b'{"schema_version":',
    )

    assert response.status_code == 422


def test_invalid_project_key_returns_401(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(key="incorrect-key"),
        json=valid_event(),
    )

    assert response.status_code == 401


def test_unknown_project_returns_401(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client
    unknown_project = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    response = client.post(
        f"/api/{unknown_project}/envelope",
        headers=headers(project_id=unknown_project),
        json=valid_event(),
    )

    assert response.status_code == 401


def test_project_header_mismatch_returns_401(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(project_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")),
        json=valid_event(),
    )

    assert response.status_code == 401


def test_duplicate_event_is_idempotently_accepted(api_client: tuple[TestClient, FakePool]) -> None:
    client, pool = api_client
    payload = valid_event()

    first = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)
    second = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["duplicate"] is True
    assert len(pool.events) == 1


def test_oversized_content_length_returns_413(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers={**headers(), "Content-Length": str(201 * 1024)},
        content=b"x",
    )

    assert response.status_code == 413


def test_oversized_stream_without_content_length_returns_413(
    api_client: tuple[TestClient, FakePool],
) -> None:
    client, _ = api_client
    payload = b"x" * (200 * 1024 + 1)

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=payload,
    )

    assert response.status_code == 413
    assert len(pool.events) == 0


def test_missing_project_key_returns_422(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client
    request_headers = {"Content-Type": "application/json"}

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=request_headers,
        json=valid_event(),
    )

    assert response.status_code == 422
