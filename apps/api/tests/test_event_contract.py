import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.events import EventLimits, EventV1, validate_event_payload

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"


def valid_payload() -> dict[str, object]:
    return json.loads((FIXTURES / "event-v1.valid.json").read_text())


def test_shared_valid_fixture_matches_server_contract() -> None:
    event = EventV1.model_validate(valid_payload())
    assert event.schema_version == 1
    assert event.level == "error"
    assert len(event.exception.stacktrace) == 1


@pytest.mark.parametrize("case", json.loads((FIXTURES / "event-v1.invalid.json").read_text()))
def test_shared_invalid_fixtures_are_rejected(case: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EventV1.model_validate(case["payload"])


def test_payload_byte_limit_is_enforced() -> None:
    payload = json.dumps(valid_payload()).encode()
    with pytest.raises(ValueError, match="payload exceeds"):
        validate_event_payload(payload, EventLimits(max_payload_bytes=len(payload) - 1))


def test_stack_depth_limit_is_configurable() -> None:
    payload = valid_payload()
    exception = payload["exception"]
    assert isinstance(exception, dict)
    exception["stacktrace"] = [exception["stacktrace"][0], exception["stacktrace"][0]]
    with pytest.raises(ValidationError, match="stacktrace exceeds"):
        EventV1.model_validate(payload, context={"limits": EventLimits(max_stack_depth=1)})


def test_breadcrumb_limit_is_configurable() -> None:
    payload = valid_payload()
    payload["breadcrumbs"] = [{}, {}]
    with pytest.raises(ValidationError, match="breadcrumbs exceed"):
        EventV1.model_validate(payload, context={"limits": EventLimits(max_breadcrumbs=1)})


def test_string_length_limit_is_configurable() -> None:
    payload = valid_payload()
    payload["platform"] = "x" * 9
    with pytest.raises(ValidationError, match="maximum string length"):
        EventV1.model_validate(payload, context={"limits": EventLimits(max_string_length=8)})


def test_unknown_nested_fields_are_rejected() -> None:
    payload = valid_payload()
    exception = payload["exception"]
    assert isinstance(exception, dict)
    exception["unexpected"] = True
    with pytest.raises(ValidationError):
        EventV1.model_validate(payload)
