from __future__ import annotations

import asyncio
import signal
from collections.abc import Mapping

import structlog

from sd_agent.config import Settings
from sd_agent.logging_config import configure_logging
from sd_agent.outbox import OutboxHandler
from sd_agent.runtime import RuntimeResources
from sd_agent.scheduler.service import JobHandler
from sd_agent.worker.outbox import OutboxWorker
from sd_agent.worker.scheduler import SchedulerWorker

logger = structlog.get_logger()


async def run_worker(
    settings: Settings | None = None,
    *,
    job_handlers: Mapping[str, JobHandler] | None = None,
    outbox_handlers: Mapping[str, OutboxHandler] | None = None,
) -> None:
    active_settings = settings or Settings()
    configure_logging(active_settings.LOG_LEVEL)
    resources = RuntimeResources.create(
        active_settings,
        job_handlers=job_handlers,
        outbox_handlers=outbox_handlers,
    )
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
        outbox_enabled=active_settings.OUTBOX_ENABLED,
    )
    outbox_task: asyncio.Task[None] | None = None
    scheduler_worker: SchedulerWorker | None = None
    if active_settings.CRON_ENABLED and resources.scheduler is None:
        await resources.close()
        raise RuntimeError("CRON_ENABLED requires database and all registered job handlers")
    if active_settings.OUTBOX_ENABLED and resources.outbox is None:
        await resources.close()
        raise RuntimeError("OUTBOX_ENABLED requires database and registered handlers")
    try:
        if active_settings.CRON_ENABLED:
            assert resources.scheduler is not None
            scheduler_worker = SchedulerWorker(resources.scheduler, logger=logger)
            await scheduler_worker.start()
        if active_settings.OUTBOX_ENABLED:
            assert resources.outbox is not None
            outbox_task = asyncio.create_task(
                OutboxWorker(
                    processor=resources.outbox,
                    logger=logger,
                    batch_size=active_settings.OUTBOX_BATCH_SIZE,
                    poll_seconds=active_settings.OUTBOX_POLL_SECONDS,
                ).run(stop),
                name="outbox-worker",
            )
        await stop.wait()
    finally:
        if scheduler_worker is not None:
            await scheduler_worker.stop()
        if outbox_task is not None:
            stop.set()
            await outbox_task
        await resources.close()
        await logger.ainfo("worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
