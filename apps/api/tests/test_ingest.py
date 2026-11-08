import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"
PROJECT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_KEY = "test-public-project-key"


class FakePool:
    def __init__(self) -> None:
        self.valid_key_hash = hashlib.sha256(PROJECT_KEY.encode()).hexdigest()
        self.closed = False

    async def close(self) -> None:
        self.closed = True

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

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"


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


def test_valid_event_is_accepted_and_stored(
    api_client: tuple[TestClient, FakePool, FakeRedis],
) -> None:
    client, pool, redis = api_client
    payload = valid_event()

    response = client.post(
        f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "event_id": payload["event_id"]}
    assert len(redis.entries) == 1
    stream, fields = redis.entries[0]
    assert stream == "events.raw"
    assert fields["project_id"] == str(PROJECT_ID)
    assert fields["event_id"] == payload["event_id"]
    assert json.loads(fields["payload"]) == payload


def test_invalid_event_payload_returns_422(
    api_client: tuple[TestClient, FakePool],
) -> None:
    client, _ = api_client
    payload = valid_event()
    payload["level"] = "critical"

    response = client.post(
        f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload
    )

    assert response.status_code == 422


def test_invalid_json_returns_422(api_client: tuple[TestClient, FakePool]) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=b'{"schema_version":',
    )

    assert response.status_code == 422


def test_invalid_project_key_returns_401(
    api_client: tuple[TestClient, FakePool],
) -> None:
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


def test_project_header_mismatch_returns_401(
    api_client: tuple[TestClient, FakePool],
) -> None:
    client, _ = api_client

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(project_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")),
        json=valid_event(),
    )

    assert response.status_code == 401


def test_duplicate_event_is_idempotently_accepted(
    api_client: tuple[TestClient, FakePool],
) -> None:
    client, pool = api_client
    payload = valid_event()

    first = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)
    second = client.post(f"/api/{PROJECT_ID}/envelope", headers=headers(), json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(redis.entries) == 2
    assert redis.entries[0][1]["event_id"] == redis.entries[1][1]["event_id"]


def test_oversized_content_length_returns_413(
    api_client: tuple[TestClient, FakePool],
) -> None:
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
    client, _pool, redis = api_client
    payload = b"x" * (200 * 1024 + 1)

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=headers(),
        content=iter([payload]),
    )

    assert response.status_code == 413
    assert len(redis.entries) == 0


def test_missing_project_key_returns_401(
    api_client: tuple[TestClient, FakePool],
) -> None:
    client, _ = api_client
    request_headers = {"Content-Type": "application/json"}

    response = client.post(
        f"/api/{PROJECT_ID}/envelope",
        headers=request_headers,
        json=valid_event(),
    )

    assert response.status_code == 401
