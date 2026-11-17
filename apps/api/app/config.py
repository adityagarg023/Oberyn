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

    # Per-project token bucket: capacity is the allowed burst, refill is events/sec.
    ingest_bucket_capacity: int = Field(default=120, gt=0)
    ingest_bucket_refill_per_second: float = Field(default=2.0, gt=0)
    ingest_spike_window_seconds: int = Field(default=60, gt=0)
    ingest_spike_max_events: int = Field(default=600, gt=0)
    ingest_monthly_event_quota: int = Field(default=1_000_000, gt=0)
    ingest_max_stream_length: int = Field(default=100_000, gt=0)
    dashboard_rate_limit_window_seconds: int = Field(default=60, gt=0)
    dashboard_rate_limit_requests: int = Field(default=120, gt=0)

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
        "ingest_bucket_capacity": source.get("INGEST_BUCKET_CAPACITY", "120"),
        "ingest_bucket_refill_per_second": source.get("INGEST_BUCKET_REFILL_PER_SECOND", "2"),
        "ingest_spike_window_seconds": source.get("INGEST_SPIKE_WINDOW_SECONDS", "60"),
        "ingest_spike_max_events": source.get("INGEST_SPIKE_MAX_EVENTS", "600"),
        "ingest_monthly_event_quota": source.get("INGEST_MONTHLY_EVENT_QUOTA", "1000000"),
        "ingest_max_stream_length": source.get("INGEST_MAX_STREAM_LENGTH", "100000"),
        "dashboard_rate_limit_window_seconds": source.get(
            "DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", "60"
        ),
        "dashboard_rate_limit_requests": source.get("DASHBOARD_RATE_LIMIT_REQUESTS", "120"),
    }

    if "ENVIRONMENT" in source:
        values["environment"] = source["ENVIRONMENT"]

    if "LOG_LEVEL" in source:
        values["log_level"] = source["LOG_LEVEL"]

    return Settings.model_validate(values)
