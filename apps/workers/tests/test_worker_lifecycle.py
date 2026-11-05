import asyncio

from workers.config import load_settings
from workers.main import run


def test_load_settings_defaults_when_env_is_empty() -> None:
    settings = load_settings({})

    assert settings.environment == "development"
    assert settings.log_level == "INFO"


async def test_run_exits_when_stop_is_set() -> None:
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(run(load_settings({}), stop), timeout=1)
