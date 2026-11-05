import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseModel):
    environment: Environment = "development"
    log_level: LogLevel = "INFO"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    values: dict[str, Any] = {}
    if "ENVIRONMENT" in source:
        values["environment"] = source["ENVIRONMENT"]
    if "LOG_LEVEL" in source:
        values["log_level"] = source["LOG_LEVEL"]
    return Settings.model_validate(values)
