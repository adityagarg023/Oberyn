import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.events import EventLimits, EventV1, validate_event_payload

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"


def valid_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "event-v1.valid.json").read_text())


def validate_dict(payload: dict[str, Any], limits: EventLimits | None = None) -> EventV1:
    return EventV1.model_validate_json(
        json.dumps(payload),
        context={"limits": limits or EventLimits()},
    )


def test_shared_valid_fixture_matches_server_contract() -> None:
    event = validate_dict(valid_payload())
    assert event.schema_version == 1
    assert event.level == "error"
    assert len(event.exception.stacktrace) == 1


@pytest.mark.parametrize("case", json.loads((FIXTURES / "event-v1.invalid.json").read_text()))
def test_shared_invalid_fixtures_are_rejected(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate_dict(case["payload"])


def test_payload_byte_limit_is_enforced() -> None:
    payload = json.dumps(valid_payload()).encode()
    with pytest.raises(ValueError, match="payload exceeds"):
        validate_event_payload(payload, EventLimits(max_payload_bytes=len(payload) - 1))


def test_stack_depth_limit_is_configurable() -> None:
    payload = valid_payload()
    frame = payload["exception"]["stacktrace"][0]
    payload["exception"]["stacktrace"] = [frame, frame]
    with pytest.raises(ValidationError, match="stacktrace exceeds"):
        validate_dict(payload, EventLimits(max_stack_depth=1))


def test_breadcrumb_limit_is_configurable() -> None:
    payload = valid_payload()
    payload["breadcrumbs"] = [{}, {}]
    with pytest.raises(ValidationError, match="breadcrumbs exceed"):
        validate_dict(payload, EventLimits(max_breadcrumbs=1))


def test_string_length_limit_is_configurable() -> None:
    payload = valid_payload()
    payload["platform"] = "x" * 9
    with pytest.raises(ValidationError, match="maximum string length"):
        validate_dict(payload, EventLimits(max_string_length=8))


def test_unknown_nested_fields_are_rejected() -> None:
    payload = valid_payload()
    payload["exception"]["unexpected"] = True
    with pytest.raises(ValidationError):
        validate_dict(payload)


def test_environment_can_override_event_limits() -> None:
    from app.config import load_settings

    settings = load_settings({
        "DATABASE_URL": "postgresql://localhost/test",
        "EVENT_MAX_PAYLOAD_BYTES": "1024",
        "EVENT_MAX_STACK_DEPTH": "12",
        "EVENT_MAX_BREADCRUMBS": "8",
        "EVENT_MAX_STRING_LENGTH": "512",
    })
    assert settings.event_max_payload_bytes == 1024
    assert settings.event_max_stack_depth == 12
    assert settings.event_max_breadcrumbs == 8
    assert settings.event_max_string_length == 512
    assert settings.event_limits.max_payload_bytes == 1024
    assert settings.event_limits.max_stack_depth == 12
