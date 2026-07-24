from __future__ import annotations

from datetime import datetime

import pytest

from sd_agent.config import Settings
from sd_agent.runtime import RuntimeResources
from sd_agent.scheduler.catalog import JOB_SPECS
from sd_agent.scheduler.service import JobOutcome


async def handler(_scheduled_for: datetime) -> JobOutcome:
    return JobOutcome({})


def test_runtime_rejects_partial_or_unknown_job_registry_before_allocating_resources() -> None:
    settings = Settings(_env_file=None, ENV="test")
    with pytest.raises(ValueError, match="missing=.*weekly_snapshot"):
        RuntimeResources.create(settings, job_handlers={"urge_scan": handler})
    complete = {spec.name: handler for spec in JOB_SPECS}
    complete["unknown"] = handler
    with pytest.raises(ValueError, match="unknown=.*unknown"):
        RuntimeResources.create(settings, job_handlers=complete)


async def test_runtime_does_not_build_scheduler_without_database() -> None:
    resources = RuntimeResources.create(
        Settings(_env_file=None, ENV="test"),
        job_handlers={spec.name: handler for spec in JOB_SPECS},
    )
    try:
        assert resources.scheduler is None
    finally:
        await resources.close()


async def test_runtime_registers_the_offline_oa_handler_only_when_explicit() -> None:
    resources = RuntimeResources.create(
        Settings(
            _env_file=None,
            ENV="test",
            OA_MODE="mock",
            SD_APP_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1/test",
        )
    )
    try:
        assert resources.outbox is not None
    finally:
        await resources.close()


def test_runtime_rejects_duplicate_mock_oa_registration() -> None:
    with pytest.raises(ValueError, match="duplicate oa.complete_pending"):
        RuntimeResources.create(
            Settings(_env_file=None, ENV="test", OA_MODE="mock"),
            outbox_handlers={"oa.complete_pending": lambda _item: None},  # type: ignore[dict-item]
        )
