import asyncio
import logging
import signal

from workers.config import Settings, load_settings

logger = logging.getLogger("oberyn.workers")


async def run(settings: Settings, stop: asyncio.Event) -> None:
    logging.basicConfig(level=settings.log_level)
    logger.info("workers started environment=%s", settings.environment)
    await stop.wait()
    logger.info("workers stopped")


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run(load_settings(), stop)


def cli() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    cli()
