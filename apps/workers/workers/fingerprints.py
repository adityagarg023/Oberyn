"""Deterministic issue fingerprints for normalized Oberyn events."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from workers.stacktrace import normalize_filename, normalize_function

_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>'\"\\)]+")
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_exception_message(message: str) -> str:
    """Replace volatile values so equivalent exception messages group together."""
    value = message.strip().lower()
    value = _URL_RE.sub("<url>", value)
    value = _UUID_RE.sub("<uuid>", value)
    value = _NUMBER_RE.sub("<number>", value)
    return _WHITESPACE_RE.sub(" ", value)


def _frame_pair(frame: Mapping[str, Any]) -> tuple[str, str] | None:
    if frame.get("in_app") is not True:
        return None
    filename = frame.get("filename")
    function = frame.get("function")
    if not isinstance(filename, str) or not isinstance(function, str):
        return None
    module = normalize_filename(filename)
    if not module:
        return None
    # Query/hash and source coordinates are excluded; only module/function matter.
    return module, normalize_function(function)


def generate_fingerprint(
    exception: Mapping[str, Any],
    *,
    custom_fingerprint: str | None = None,
    top_n: int = 5,
) -> str:
    """Return a SHA-256 grouping key from exception type and top in-app frames.

    A supplied custom fingerprint is authoritative and is hashed as-is after
    trimming. Without usable frames, a normalized exception message is used.
    """
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if custom_fingerprint is not None:
        custom = custom_fingerprint.strip()
        if not custom:
            raise ValueError("custom_fingerprint must not be empty")
        canonical = "custom\0" + custom
    else:
        exception_type = exception.get("type")
        kind = exception_type.strip().lower() if isinstance(exception_type, str) else "error"
        stack = exception.get("stacktrace")
        pairs: list[tuple[str, str]] = []
        if isinstance(stack, Sequence) and not isinstance(stack, (str, bytes)):
            for frame in stack:
                if isinstance(frame, Mapping):
                    pair = _frame_pair(frame)
                    if pair is not None:
                        pairs.append(pair)
                        if len(pairs) == top_n:
                            break
        if pairs:
            canonical = "trace\0" + kind + "\0" + "\n".join(
                f"{module}\0{function}" for module, function in pairs
            )
        else:
            message = exception.get("value")
            normalized = normalize_exception_message(message if isinstance(message, str) else "")
            canonical = "message\0" + kind + "\0" + normalized
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
