from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app


def test_health_reports_ok() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_load_settings_defaults_when_env_is_empty() -> None:
    settings = load_settings({})

    assert settings.environment == "development"
    assert settings.log_level == "INFO"


def test_load_settings_reads_environment_values() -> None:
    settings = load_settings({"ENVIRONMENT": "production", "LOG_LEVEL": "WARNING"})

    assert settings.environment == "production"
    assert settings.log_level == "WARNING"
