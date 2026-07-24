from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from sd_agent.scheduler.admin import (
    JobRunSummary,
    SchedulerAdminActor,
    SchedulerAdminError,
    SchedulerAdminService,
    TriggerResult,
)

KEY = UUID("11111111-1111-4111-8111-111111111111")


class FakeRepository:
    def __init__(self) -> None:
        self.payload_hash = ""

    async def list_runs(
        self, *, cursor: str | None, limit: int
    ) -> tuple[tuple[JobRunSummary, ...], None]:
        del cursor, limit
        now = datetime(2026, 7, 24, tzinfo=UTC)
        return ((JobRunSummary("run-1", "urge_scan", now, "succeeded", {}, None, now, now)),), None

    async def enqueue_trigger(self, **kwargs: object) -> TriggerResult:
        self.payload_hash = str(kwargs["payload_hash"])
        return TriggerResult(
            "trigger-1",
            "outbox-1",
            str(kwargs["job"]),
            kwargs["now"],  # type: ignore[arg-type]
            kwargs["retry_of_job_run_id"],  # type: ignore[arg-type]
            "queued",
            False,
        )


def actor(*roles: str) -> SchedulerAdminActor:
    return SchedulerAdminActor(7, frozenset(roles), "req-1", None, None)


async def test_admin_lists_runs_and_enqueues_known_job() -> None:
    repository = FakeRepository()
    service = SchedulerAdminService(repository)

    page = await service.list_runs(actor=actor("ops_admin"), cursor=None, limit=25)
    result = await service.trigger(
        job="urge_scan",
        retry_of_job_run_id=None,
        idempotency_key=KEY,
        actor=actor("supervision_admin"),
        now=datetime(2026, 7, 24, tzinfo=UTC),
    )

    assert page.items[0].job_run_id == "run-1"
    assert result.state == "queued"
    assert len(repository.payload_hash) == 64


@pytest.mark.parametrize("role", ["leader", "domain_owner"])
async def test_admin_rejects_unprivileged_roles(role: str) -> None:
    service = SchedulerAdminService(FakeRepository())

    with pytest.raises(SchedulerAdminError, match="JOB_RUN_FORBIDDEN"):
        await service.list_runs(actor=actor(role), cursor=None, limit=25)


async def test_admin_rejects_unknown_job_and_invalid_limit() -> None:
    service = SchedulerAdminService(FakeRepository())

    with pytest.raises(SchedulerAdminError, match="JOB_NOT_FOUND"):
        await service.trigger(
            job="unknown",
            retry_of_job_run_id=None,
            idempotency_key=KEY,
            actor=actor("ops_admin"),
            now=datetime(2026, 7, 24, tzinfo=UTC),
        )
    with pytest.raises(SchedulerAdminError, match="JOB_RUN_LIMIT_INVALID"):
        await service.list_runs(actor=actor("ops_admin"), cursor=None, limit=0)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        await service.trigger(
            job="urge_scan",
            retry_of_job_run_id=None,
            idempotency_key=KEY,
            actor=actor("ops_admin"),
            now=datetime(2026, 7, 24),
        )
