import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseModel):
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    database_url: str = Field(default="", min_length=1)
    redis_url: str = "redis://localhost:6379/0"
    event_stream_name: str = "events.raw"
    consumer_group: str = "oberyn-workers"
    consumer_name: str = Field(default_factory=lambda: f"worker-{os.getpid()}")
    read_count: int = Field(default=25, gt=0, le=500)
    block_ms: int = Field(default=1000, gt=0)
    claim_idle_ms: int = Field(default=60_000, gt=0)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    values: dict[str, Any] = {
        "database_url": source.get("DATABASE_URL", ""),
        "redis_url": source.get("REDIS_URL", "redis://localhost:6379/0"),
        "event_stream_name": source.get("EVENT_STREAM_NAME", "events.raw"),
        "consumer_group": source.get("EVENT_CONSUMER_GROUP", "oberyn-workers"),
        "read_count": source.get("EVENT_READ_COUNT", "25"),
        "block_ms": source.get("EVENT_BLOCK_MS", "1000"),
        "claim_idle_ms": source.get("EVENT_CLAIM_IDLE_MS", "60000"),
    }
    if "EVENT_CONSUMER_NAME" in source:
        values["consumer_name"] = source["EVENT_CONSUMER_NAME"]
    if "ENVIRONMENT" in source:
        values["environment"] = source["ENVIRONMENT"]
    if "LOG_LEVEL" in source:
        values["log_level"] = source["LOG_LEVEL"]
    return Settings.model_validate(values)
