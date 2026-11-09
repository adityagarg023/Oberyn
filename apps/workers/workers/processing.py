from __future__ import annotations

import re
from typing import Any

from app.contracts.events import EventV1

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\\b(password|token|secret|api[_-]?key)\\s*[:=]\\s*[^\\s,;]+"
)
_QUERY = re.compile(r"([?&](?:token|key|secret|password|signature)=)[^&#\\s]+")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[Filtered]" if _SENSITIVE_KEY.search(str(key)) else _scrub(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_scrub(child) for child in value]
    if isinstance(value, str):
        value = _BEARER.sub("Bearer [Filtered]", value)
        value = _SECRET_ASSIGNMENT.sub(r"\\1=[Filtered]", value)
        return _QUERY.sub(r"\\1[Filtered]", value)
    return value


def normalize_event(event: EventV1) -> EventV1:
    """Return a normalized, scrubbed copy without mutating the validated input."""
    payload = event.model_dump(mode="python")
    payload["breadcrumbs"] = _scrub(payload["breadcrumbs"])
    payload["tags"] = _scrub(payload["tags"])
    payload["request"] = _scrub(payload["request"])
    payload["exception"]["value"] = _scrub(payload["exception"]["value"])
    for frame in payload["exception"]["stacktrace"]:
        frame["filename"] = frame["filename"].strip()
        frame["function"] = " ".join(frame["function"].split()) or "<anonymous>"
    return EventV1.model_validate(payload)


def parse_stacktrace(event: EventV1) -> tuple[dict[str, Any], ...]:
    """Expose validated SDK stack frames in a stable, storage-ready representation."""
    return tuple(
        {
            "filename": frame.filename.strip(),
            "function": " ".join(frame.function.split()) or "<anonymous>",
            "lineno": frame.lineno,
            "colno": frame.colno,
            "in_app": frame.in_app,
        }
        for frame in event.exception.stacktrace
    )
