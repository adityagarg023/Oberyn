from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, load_settings
from app.main import create_app, get_database_engine, get_redis_client


def test_health_reports_ok() -> None:
    settings = Settings(database_url="postgresql://localhost/test")
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_load_settings_defaults_when_env_is_empty() -> None:
    settings = load_settings({"DATABASE_URL": "postgresql://localhost/test"})

    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.redis_url == "redis://localhost:6379/0"


def test_load_settings_reads_environment_values() -> None:
    settings = load_settings(
        {
            "ENVIRONMENT": "production",
            "LOG_LEVEL": "WARNING",
            "DATABASE_URL": "postgresql://localhost/test",
        }
    )

    assert settings.environment == "production"
    assert settings.log_level == "WARNING"


def test_load_settings_requires_database_url() -> None:
    with pytest.raises(ValidationError):
        load_settings({})


def test_readiness_reports_database_ok() -> None:
    settings = Settings(database_url="postgresql://localhost/test")
    app = create_app(settings)
    app.dependency_overrides[get_redis_client] = lambda: FakeRedis()
    app.dependency_overrides[get_database_engine] = lambda: FakeEngine()

    client = TestClient(app)
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "redis": "ok",
    }


def test_readiness_reports_database_unavailable() -> None:
    settings = Settings(database_url="postgresql://localhost/test")
    app = create_app(settings)

    app.dependency_overrides[get_database_engine] = lambda: FailedEngine()
    app.dependency_overrides[get_redis_client] = lambda: FakeRedis()

    client = TestClient(app)
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_readiness_reports_redis_unavailable() -> None:
    settings = Settings(database_url="postgresql://localhost/test")
    app = create_app(settings)

    app.dependency_overrides[get_database_engine] = lambda: FakeEngine()
    app.dependency_overrides[get_redis_client] = lambda: FailedRedis()

    client = TestClient(app)
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Redis unavailable"}


class FakeRedis:
    async def ping(self) -> bool:
        return True


class FailedRedis:
    async def ping(self) -> bool:
        raise RedisError("Connection failed")


class FakeConnection:
    async def execute(self, statement: Any) -> None:
        return None


class FakeConnectionContext:
    async def __aenter__(self) -> FakeConnection:
        return FakeConnection()

    async def __aexit__(
        self,
        exc_type: Any,
        exc: Any,
        traceback: Any,
    ) -> bool:
        return False


class FakeEngine:
    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext()


class FailedEngine:
    def connect(self) -> FakeConnectionContext:
        raise SQLAlchemyError("Connection failed")
