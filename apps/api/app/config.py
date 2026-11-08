import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.contracts.events import EventLimits

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseModel):
    environment: Environment = "development"
    log_level: LogLevel = "INFO"

    database_url: str = Field(min_length=1)
    redis_url: str = "redis://localhost:6379/0"
    event_stream_name: str = "events.raw"

    event_max_payload_bytes: int = Field(default=200 * 1024, gt=0)
    event_max_stack_depth: int = Field(default=50, gt=0)
    event_max_breadcrumbs: int = Field(default=100, ge=0)
    event_max_string_length: int = Field(default=4096, gt=0)

    @property
    def event_limits(self) -> EventLimits:
        return EventLimits(
            max_payload_bytes=self.event_max_payload_bytes,
            max_stack_depth=self.event_max_stack_depth,
            max_breadcrumbs=self.event_max_breadcrumbs,
            max_string_length=self.event_max_string_length,
        )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env

    values: dict[str, Any] = {
        "database_url": source.get("DATABASE_URL", ""),
        "redis_url": source.get("REDIS_URL", "redis://localhost:6379/0"),
        "event_stream_name": source.get("EVENT_STREAM_NAME", "events.raw"),
        "event_max_payload_bytes": source.get("EVENT_MAX_PAYLOAD_BYTES", "204800"),
        "event_max_stack_depth": source.get("EVENT_MAX_STACK_DEPTH", "50"),
        "event_max_breadcrumbs": source.get("EVENT_MAX_BREADCRUMBS", "100"),
        "event_max_string_length": source.get("EVENT_MAX_STRING_LENGTH", "4096"),
    }

    if "ENVIRONMENT" in source:
        values["environment"] = source["ENVIRONMENT"]

    if "LOG_LEVEL" in source:
        values["log_level"] = source["LOG_LEVEL"]

    return Settings.model_validate(values)
