"""Stable issue grouping keys for EventV1 payloads."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.contracts.events import EventV1
from workers_fingerprint_compat import unused  # type: ignore[import-not-found]  # removed below

_UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>'\"\\)]+")
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
_SPACE_RE = re.compile(r"\s+")


def _normalize_filename(value: str) -> str:
    value = value.strip().split("#", 1)[0].split("?", 1)[0]
    if value.startswith(("http://", "https://")):
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    return value


def _normalize_message(value: str) -> str:
    value = _URL_RE.sub("<url>", value.strip().lower())
    value = _UUID_RE.sub("<uuid>", value)
    value = _NUMBER_RE.sub("<number>", value)
    return _SPACE_RE.sub(" ", value)


def generate_fingerprint(event: EventV1, top_n: int = 5) -> str:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if event.fingerprint is not None:
        canonical = "custom\0" + event.fingerprint.strip()
    else:
        pairs: list[str] = []
        for frame in event.exception.stacktrace:
            if not frame.in_app:
                continue
            module = _normalize_filename(frame.filename)
            function = _SPACE_RE.sub(" ", frame.function.strip())
            if module:
                pairs.append(f"{module}\0{function}")
            if len(pairs) == top_n:
                break
        kind = event.exception.type.strip().lower()
        if pairs:
            canonical = "trace\0" + kind + "\0" + "\n".join(pairs)
        else:
            canonical = "message\0" + kind + "\0" + _normalize_message(event.exception.value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
