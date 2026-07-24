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
        assert resources.sso is None
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


@pytest.mark.parametrize("kind", ["oa.complete_pending", "oa.send_urge"])
def test_runtime_rejects_duplicate_mock_oa_registration(kind: str) -> None:
    with pytest.raises(ValueError, match="duplicate offline OA handlers"):
        RuntimeResources.create(
            Settings(_env_file=None, ENV="test", OA_MODE="mock"),
            outbox_handlers={kind: lambda _item: None},  # type: ignore[dict-item]
        )


async def test_runtime_builds_sso_stub_only_with_required_local_dependencies() -> None:
    resources = RuntimeResources.create(
        Settings(
            _env_file=None,
            ENV="test",
            SSO_MODE="stub",
            SSO_STUB_PERSON_ID=9,
            SD_APP_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1/test",
            TEABLE_BASE_URL="http://127.0.0.1:3000",
            TEABLE_TOKEN="local-token",  # noqa: S106 -- 测试替身，不是凭据
            TEABLE_TABLE_IDS={
                name: f"tbl_{name}"
                for name in (
                    "org_unit",
                    "person",
                    "role_assignment",
                    "key_work",
                    "task",
                    "task_owner",
                    "progress_log",
                    "urge_log",
                    "work_calendar",
                )
            },
            JWT_SECRET_V1="j" * 32,
        )
    )
    try:
        assert resources.sso is not None
    finally:
        await resources.close()
