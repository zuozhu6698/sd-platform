from __future__ import annotations

import asyncio
import signal

import structlog

from sd_agent.config import Settings
from sd_agent.logging_config import configure_logging
from sd_agent.runtime import RuntimeResources

logger = structlog.get_logger()


async def run_worker(settings: Settings | None = None) -> None:
    active_settings = settings or Settings()
    configure_logging(active_settings.LOG_LEVEL)
    resources = RuntimeResources.create(active_settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass

    await logger.ainfo(
        "worker_started",
        environment=active_settings.ENV.value,
        scheduler_enabled=active_settings.CRON_ENABLED,
    )
    try:
        await stop.wait()
    finally:
        await resources.close()
        await logger.ainfo("worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
