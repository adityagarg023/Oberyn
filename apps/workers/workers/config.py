import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseModel):
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    event_stream_name: str = "events.raw"
    consumer_group: str = "oberyn-workers"
    consumer_name: str = Field(default_factory=lambda: f"worker-{os.getpid()}")
    dead_letter_stream_name: str = "events.dead"
    failure_hash_name: str = "events:failures"
    batch_size: int = Field(default=100, gt=0, le=500)
    block_ms: int = Field(default=1000, gt=0)
    claim_idle_ms: int = Field(default=60_000, gt=0)
    max_attempts: int = Field(default=5, gt=0)
    retry_base_ms: int = Field(default=250, gt=0)
    retry_max_ms: int = Field(default=30_000, gt=0)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    values: dict[str, Any] = {
        "database_url": source.get("DATABASE_URL", ""),
        "redis_url": source.get("REDIS_URL", "redis://localhost:6379/0"),
        "event_stream_name": source.get("EVENT_STREAM_NAME", "events.raw"),
        "consumer_group": source.get("EVENT_CONSUMER_GROUP", "oberyn-workers"),
        "dead_letter_stream_name": source.get("EVENT_DEAD_LETTER_STREAM", "events.dead"),
        "failure_hash_name": source.get("EVENT_FAILURE_HASH", "events:failures"),
        "batch_size": source.get("EVENT_BATCH_SIZE", source.get("EVENT_READ_COUNT", "100")),
        "block_ms": source.get("EVENT_BLOCK_MS", "1000"),
        "claim_idle_ms": source.get("EVENT_CLAIM_IDLE_MS", "60000"),
        "max_attempts": source.get("EVENT_MAX_ATTEMPTS", "5"),
        "retry_base_ms": source.get("EVENT_RETRY_BASE_MS", "250"),
        "retry_max_ms": source.get("EVENT_RETRY_MAX_MS", "30000"),
    }
    if "EVENT_CONSUMER_NAME" in source:
        values["consumer_name"] = source["EVENT_CONSUMER_NAME"]
    if "ENVIRONMENT" in source:
        values["environment"] = source["ENVIRONMENT"]
    if "LOG_LEVEL" in source:
        values["log_level"] = source["LOG_LEVEL"]
    return Settings.model_validate(values)
