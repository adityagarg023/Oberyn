from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.contracts.events import validate_event_payload
from app.fingerprints import generate_fingerprint

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"


def event_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "event-v1.valid.json").read_text())


def test_equivalent_traces_ignore_line_numbers_and_url_suffixes() -> None:
    first = event_payload()
    second = event_payload()
    first["exception"]["stacktrace"][0].update(filename="https://app.test/src/main.js?v=1#one", lineno=1, colno=2)
    second["exception"]["stacktrace"][0].update(filename="https://app.test/src/main.js?v=2#two", lineno=900, colno=40)
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) == generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )


def test_different_in_app_functions_produce_different_fingerprints() -> None:
    first = event_payload()
    second = event_payload()
    second["exception"]["stacktrace"][0]["function"] = "differentHandler"
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) != generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )


def test_exception_type_is_part_of_the_fingerprint() -> None:
    first = event_payload()
    second = event_payload()
    second["exception"]["type"] = "DifferentError"
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) != generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )


def test_only_the_first_five_in_app_frames_are_used() -> None:
    first = event_payload()
    second = event_payload()
    base_frame = first["exception"]["stacktrace"][0]
    first["exception"]["stacktrace"] = [dict(base_frame, function=f"frame-{index}") for index in range(6)]
    second["exception"]["stacktrace"] = [dict(base_frame, function=f"frame-{index}") for index in range(5)]
    second["exception"]["stacktrace"].append(dict(base_frame, function="different-tail"))
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) == generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )


def test_message_fallback_normalizes_numbers_uuids_and_urls() -> None:
    first = event_payload()
    second = event_payload()
    for payload, value in (
        (first, "Request 42 failed for 123e4567-e89b-12d3-a456-426614174000 at https://host.test/a/42"),
        (second, "Request 99 failed for 123e4567-e89b-12d3-a456-426614174001 at https://host.test/a/99"),
    ):
        payload["exception"]["value"] = value
        payload["exception"]["stacktrace"] = []
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) == generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )


def test_custom_fingerprint_overrides_exception_and_stack() -> None:
    first = event_payload()
    second = event_payload()
    first["fingerprint"] = "checkout/payment"
    second["fingerprint"] = "checkout/payment"
    second["exception"]["type"] = "OtherError"
    second["exception"]["stacktrace"] = []
    assert generate_fingerprint(validate_event_payload(json.dumps(first).encode())) == generate_fingerprint(
        validate_event_payload(json.dumps(second).encode())
    )
