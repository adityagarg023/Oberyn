from workers.fingerprints import generate_fingerprint, normalize_exception_message


def frame(filename: str = "/srv/app/main.py?build=1#source", function: str = "handle", **extra: object) -> dict[str, object]:
    return {"filename": filename, "function": function, "in_app": True, **extra}


def test_equivalent_traces_ignore_line_numbers_and_url_suffixes() -> None:
    first = {"type": "TypeError", "value": "failed", "stacktrace": [frame(lineno=10, colno=2)]}
    second = {
        "type": "TypeError",
        "value": "failed",
        "stacktrace": [frame(filename="/srv/app/main.py?build=2#other", lineno=900, colno=40)],
    }
    assert generate_fingerprint(first) == generate_fingerprint(second)


def test_different_in_app_trace_produces_different_fingerprint() -> None:
    first = {"type": "TypeError", "value": "failed", "stacktrace": [frame()]}
    second = {"type": "TypeError", "value": "failed", "stacktrace": [frame(function="render")]}
    assert generate_fingerprint(first) != generate_fingerprint(second)


def test_only_top_n_in_app_frames_are_used() -> None:
    base = {"type": "RuntimeError", "value": "x", "stacktrace": [frame(), frame("/src/next.py", "next")]}
    changed_tail = {
        **base,
        "stacktrace": [frame(), frame("/src/next.py", "next"), frame("/src/ignored.py", "ignored")],
    }
    assert generate_fingerprint(base, top_n=2) == generate_fingerprint(changed_tail, top_n=2)


def test_non_app_frames_do_not_affect_fingerprint() -> None:
    base = {"type": "Error", "value": "x", "stacktrace": [frame()]}
    with_vendor = {
        **base,
        "stacktrace": [
            {"filename": "/vendor/runtime.js", "function": "run", "in_app": False},
            frame(),
        ],
    }
    assert generate_fingerprint(base) == generate_fingerprint(with_vendor)


def test_message_fallback_normalizes_numbers_uuids_and_urls() -> None:
    first = {
        "type": "ValueError",
        "value": "User 42 failed for 123e4567-e89b-12d3-a456-426614174000 at https://example.test/users/42?q=1",
        "stacktrace": [],
    }
    second = {
        "type": "ValueError",
        "value": "User 99 failed for 123e4567-e89b-12d3-a456-426614174001 at https://example.test/users/99?q=2",
        "stacktrace": [],
    }
    assert normalize_exception_message(first["value"]) == normalize_exception_message(second["value"])
    assert generate_fingerprint(first) == generate_fingerprint(second)


def test_custom_fingerprint_is_authoritative_and_deterministic() -> None:
    event = {"type": "Error", "value": "different", "stacktrace": [frame()]}
    assert generate_fingerprint(event, custom_fingerprint="checkout/payment") == generate_fingerprint(
        {"type": "Other", "value": "anything", "stacktrace": []},
        custom_fingerprint="checkout/payment",
    )
    assert generate_fingerprint(event, custom_fingerprint="checkout/payment") != generate_fingerprint(
        event, custom_fingerprint="checkout/cart"
    )
