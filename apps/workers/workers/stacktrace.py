"""Stack-trace parsing, normalization, and fingerprint helpers.

The parser accepts the common textual formats emitted by V8/Chrome/Node,
Firefox, and Safari. It is intentionally tolerant: malformed lines are skipped
rather than causing event processing to fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Iterable, Mapping

_LOCATION_RE = re.compile(r"^(?P<file>.*?)(?::(?P<line>\d+))(?::(?P<column>\d+))?$")
_V8_RE = re.compile(r"^\s*at\s+(?:(?P<function>.*?)\s+\()?((?P<location>.+?))\)?\s*$")
_GECKO_RE = re.compile(r"^\s*(?P<function>.*?)@(?P<location>.+?)\s*$")
_INTERNAL_PREFIXES = ("node:internal", "internal/", "native", "webpack://")
_LIBRARY_PARTS = (
    "/node_modules/", "/bower_components/", "/vendor/", "/polyfills/",
    "/core-js/", "/zone.js",
)
_BROWSER_INTERNALS = ("<anonymous>", "native code", "extensions::", "resource://")
_ANONYMOUS = {"", "?", "anonymous", "<anonymous>", "unknown"}


@dataclass(frozen=True, slots=True)
class StackFrame:
    filename: str
    function: str
    lineno: int | None = None
    colno: int | None = None
    in_app: bool = True


def normalize_filename(filename: str) -> str:
    """Remove URL query/hash noise while retaining a stable path."""
    value = filename.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "file://")):
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    return value.split("#", 1)[0].split("?", 1)[0]


def normalize_function(function: str | None) -> str:
    """Collapse runtime-specific wrappers and anonymous function labels."""
    value = (function or "").strip()
    value = re.sub(r"^(?:async\s+)?new\s+", "", value)
    value = re.sub(r"^Object\.<anonymous>$", "<anonymous>", value)
    value = re.sub(r"^Module\._compile$", "Module._compile", value)
    value = re.sub(r"\s+", " ", value)
    return "<anonymous>" if value.lower() in _ANONYMOUS else value


def _parse_location(location: str) -> tuple[str, int | None, int | None] | None:
    location = location.strip().strip("()")
    if not location or location in {"native", "native code"}:
        return None
    match = _LOCATION_RE.match(location)
    if match is None:
        # A filename without line/column is still useful for grouping.
        if location.startswith(("http://", "https://", "file://")):
            return normalize_filename(location), None, None
        if ":" not in location and location.startswith(("/", "./", "../")):
            return normalize_filename(location), None, None
        return None
    filename = normalize_filename(match.group("file"))
    if not filename:
        return None
    return filename, int(match.group("line")), (
        int(match.group("column")) if match.group("column") is not None else None
    )


def _parse_line(line: str) -> StackFrame | None:
    value = line.rstrip()
    if not value.strip() or value.lstrip().startswith(("Error", "TypeError", "ReferenceError")):
        return None

    function = ""
    location = ""
    v8 = _V8_RE.match(value)
    if v8 is not None:
        function = (v8.group("function") or "").strip()
        location = v8.group("location").strip()
    else:
        gecko = _GECKO_RE.match(value)
        if gecko is not None:
            function = gecko.group("function").strip()
            location = gecko.group("location").strip()
        else:
            location = value.strip()

    parsed = _parse_location(location)
    if parsed is None:
        return None
    filename, lineno, colno = parsed
    return StackFrame(filename, normalize_function(function), lineno, colno)


def parse_stacktrace(stack: str | None) -> list[StackFrame]:
    """Parse a textual stack trace; malformed and incomplete lines are ignored."""
    if not stack:
        return []
    frames: list[StackFrame] = []
    for line in stack.splitlines():
        frame = _parse_line(line)
        if frame is not None:
            frames.append(frame)
    return frames


def is_internal_frame(frame: StackFrame) -> bool:
    filename = frame.filename.lower()
    return (
        filename.startswith(_INTERNAL_PREFIXES)
        or any(part in filename for part in _LIBRARY_PARTS)
        or any(token in filename for token in _BROWSER_INTERNALS)
    )


def select_relevant_frames(frames: Iterable[StackFrame]) -> list[StackFrame]:
    """Prefer app frames; otherwise retain non-library frames as a fallback."""
    materialized = list(frames)
    app_frames = [frame for frame in materialized if frame.in_app and not is_internal_frame(frame)]
    if app_frames:
        return app_frames
    return [frame for frame in materialized if not is_internal_frame(frame)]


def normalize_frames(frames: Iterable[StackFrame | Mapping[str, object]]) -> list[StackFrame]:
    """Normalize already-structured EventV1 frames without trusting their shape."""
    normalized: list[StackFrame] = []
    for item in frames:
        if isinstance(item, StackFrame):
            frame = item
        else:
            filename = item.get("filename")
            function = item.get("function")
            if not isinstance(filename, str) or not isinstance(function, str):
                continue
            lineno = item.get("lineno")
            colno = item.get("colno")
            in_app = item.get("in_app", True)
            frame = StackFrame(
                normalize_filename(filename),
                normalize_function(function),
                lineno if isinstance(lineno, int) and not isinstance(lineno, bool) else None,
                colno if isinstance(colno, int) and not isinstance(colno, bool) else None,
                in_app if isinstance(in_app, bool) else True,
            )
        filename = normalize_filename(frame.filename)
        if not filename:
            continue
        normalized.append(
            StackFrame(filename, normalize_function(frame.function), frame.lineno, frame.colno, frame.in_app)
        )
    return normalized


def fingerprint_components(frames: Iterable[StackFrame]) -> tuple[str, ...]:
    """Return stable frame identity; source line and column never affect grouping."""
    relevant = select_relevant_frames(frames)
    return tuple(f"{frame.filename}:{frame.function}" for frame in relevant)


def fingerprint(frames: Iterable[StackFrame]) -> str:
    """Build a deterministic, readable grouping key from normalized frames."""
    components = fingerprint_components(frames)
    return "|".join(components) if components else "unknown"
