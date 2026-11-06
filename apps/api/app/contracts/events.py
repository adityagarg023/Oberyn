from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class EventLimits(BaseModel):
    max_payload_bytes: int = Field(default=200 * 1024, gt=0)
    max_stack_depth: int = Field(default=50, gt=0)
    max_breadcrumbs: int = Field(default=100, ge=0)
    max_string_length: int = Field(default=4096, gt=0)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StackFrameV1(StrictModel):
    filename: str
    function: str
    lineno: int | None
    colno: int | None
    in_app: bool


class ExceptionV1(StrictModel):
    type: str
    value: str
    stacktrace: list[StackFrameV1]


class UserV1(StrictModel):
    id: str = Field(min_length=1, max_length=255)


class EventV1(StrictModel):
    schema_version: Literal[1]
    event_id: UUID
    timestamp: datetime
    platform: str
    level: Literal["fatal", "error", "warning", "info", "debug", "log"]
    release: str | None = None
    environment: str | None = None
    exception: ExceptionV1
    fingerprint: str | None = Field(default=None, min_length=1, max_length=128)
    breadcrumbs: list[dict[str, Any]] = Field(default_factory=list)
    user: UserV1 | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fingerprint")
    @classmethod
    def normalize_custom_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("fingerprint must not be empty")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("breadcrumbs", "request", mode="before")
    @classmethod
    def require_json_object_data(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
        raise ValueError("value must be a JSON object or an array of JSON objects")

    @model_validator(mode="after")
    def enforce_configured_limits(self, info: ValidationInfo) -> EventV1:
        limits = info.context.get("limits") if isinstance(info.context, dict) else None
        limits = limits or EventLimits()

        if len(self.exception.stacktrace) > limits.max_stack_depth:
            raise ValueError(f"stacktrace exceeds maximum depth of {limits.max_stack_depth}")
        if len(self.breadcrumbs) > limits.max_breadcrumbs:
            raise ValueError(f"breadcrumbs exceed maximum count of {limits.max_breadcrumbs}")

        def check(value: Any, path: str) -> None:
            if isinstance(value, str) and len(value) > limits.max_string_length:
                raise ValueError(f"{path} exceeds maximum string length of {limits.max_string_length}")
            if isinstance(value, dict):
                for key, child in value.items():
                    check(str(key), f"{path} key")
                    check(child, f"{path}.{key}")
            elif isinstance(value, (list, tuple)):
                for index, child in enumerate(value):
                    check(child, f"{path}[{index}]")

        check(self.model_dump(mode="python"), "event")
        return self


def validate_event_payload(payload: bytes, limits: EventLimits | None = None) -> EventV1:
    configured = limits or EventLimits()
    if len(payload) > configured.max_payload_bytes:
        raise ValueError(f"payload exceeds maximum size of {configured.max_payload_bytes} bytes")
    return EventV1.model_validate_json(payload, context={"limits": configured})
