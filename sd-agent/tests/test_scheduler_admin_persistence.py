from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sd_agent.persistence import scheduler_admin as scheduler_admin_module
from sd_agent.persistence.models import AuditEvent, JobRun, JobTriggerRequest, OutboxMessage
from sd_agent.persistence.scheduler_admin import SqlSchedulerAdminRepository
from sd_agent.scheduler.admin import SchedulerAdminActor, SchedulerAdminError

NOW = datetime(2026, 7, 24, 4, tzinfo=UTC)
KEY = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = "22222222-2222-4222-8222-222222222222"


def actor() -> SchedulerAdminActor:
    return SchedulerAdminActor(7, frozenset({"ops_admin"}), "req-1", "192.0.2.1", "ua")


def run(*, state: str = "failed", job: str = "urge_scan", run_id: str = RUN_ID) -> JobRun:
    return JobRun(
        job_run_id=run_id,
        job=job,
        scheduled_for=NOW,
        state=state,
        config_hash="a" * 64,
        counts={"seen": 1},
        error_code="JOB_EXECUTION_FAILED" if state == "failed" else None,
        started_at=NOW,
        finished_at=NOW,
    )


def trigger(*, payload_hash: str = "b" * 64, retry_of: str | None = None) -> JobTriggerRequest:
    return JobTriggerRequest(
        trigger_id="33333333-3333-4333-8333-333333333333",
        idempotency_key=str(KEY),
        payload_hash=payload_hash,
        job="urge_scan",
        scheduled_for=NOW,
        retry_of_job_run_id=retry_of,
        requested_by=7,
        outbox_id="44444444-4444-4444-8444-444444444444",
        created_at=NOW,
    )


class FakeExecuteResult:
    def __init__(self, row: tuple[datetime, str] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[datetime, str] | None:
        return self.row


class FakeSession:
    def __init__(
        self,
        *,
        scalars: list[object | None] | None = None,
        rows: list[JobRun] | None = None,
        anchor: tuple[datetime, str] | None = None,
    ) -> None:
        self.scalar_values = list(scalars or [])
        self.rows = list(rows or [])
        self.anchor = anchor
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.pop(0)

    async def scalars(self, _statement: object) -> list[JobRun]:
        return self.rows

    async def execute(self, _statement: object) -> FakeExecuteResult:
        return FakeExecuteResult(self.anchor)

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield self.session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def repository(session: FakeSession) -> SqlSchedulerAdminRepository:
    instance = object.__new__(SqlSchedulerAdminRepository)
    instance._sessions = FakeSessions(session)  # type: ignore[assignment]
    return instance


def test_repository_builds_non_expiring_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(scheduler_admin_module, "async_sessionmaker", factory)
    engine = object()
    SqlSchedulerAdminRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_list_runs_pages_with_and_without_cursor() -> None:
    rows = [run(run_id="22222222-2222-4222-8222-222222222221"), run()]
    first, cursor = await repository(FakeSession(rows=rows)).list_runs(cursor=None, limit=1)
    second, no_cursor = await repository(
        FakeSession(rows=[rows[1]], anchor=(NOW, rows[0].job_run_id))
    ).list_runs(cursor=rows[0].job_run_id, limit=1)

    assert first[0].counts == {"seen": 1}
    assert cursor == rows[0].job_run_id
    assert second[0].job_run_id == RUN_ID
    assert no_cursor is None


async def test_list_runs_rejects_unknown_cursor() -> None:
    with pytest.raises(SchedulerAdminError, match="JOB_RUN_CURSOR_INVALID"):
        await repository(FakeSession(anchor=None)).list_runs(cursor=RUN_ID, limit=1)


async def test_enqueue_trigger_is_transactional_and_audited() -> None:
    session = FakeSession(scalars=[True, None])

    result = await repository(session).enqueue_trigger(
        job="urge_scan",
        retry_of_job_run_id=None,
        idempotency_key=KEY,
        payload_hash="b" * 64,
        actor=actor(),
        now=NOW,
    )

    stored = next(value for value in session.added if isinstance(value, JobTriggerRequest))
    outbox = next(value for value in session.added if isinstance(value, OutboxMessage))
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert result.idempotent is False
    assert stored.scheduled_for.utcoffset().total_seconds() == 8 * 3600
    assert outbox.kind == "scheduler.run_job"
    assert outbox.payload["trigger_id"] == stored.trigger_id
    assert audit.what == "scheduler.job.trigger"


async def test_enqueue_trigger_reuses_matching_idempotency_key() -> None:
    session = FakeSession(scalars=[True, trigger()])
    result = await repository(session).enqueue_trigger(
        job="urge_scan",
        retry_of_job_run_id=None,
        idempotency_key=KEY,
        payload_hash="b" * 64,
        actor=actor(),
        now=NOW,
    )
    assert result.idempotent is True
    assert session.added == []


async def test_enqueue_trigger_rejects_idempotency_conflict() -> None:
    with pytest.raises(SchedulerAdminError, match="IDEMPOTENCY_CONFLICT"):
        await repository(
            FakeSession(scalars=[True, trigger(payload_hash="c" * 64)])
        ).enqueue_trigger(
            job="urge_scan",
            retry_of_job_run_id=None,
            idempotency_key=KEY,
            payload_hash="b" * 64,
            actor=actor(),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ([True, None, None], "JOB_RUN_NOT_FOUND"),
        ([True, None, run(state="succeeded")], "JOB_RUN_NOT_RETRYABLE"),
        ([True, None, run(job="weekly_report")], "JOB_RUN_NOT_RETRYABLE"),
        ([True, None, run(), trigger(retry_of=RUN_ID)], "JOB_RUN_ALREADY_RETRIED"),
    ],
)
async def test_retry_rejects_invalid_source(values: list[object | None], code: str) -> None:
    with pytest.raises(SchedulerAdminError) as caught:
        await repository(FakeSession(scalars=values)).enqueue_trigger(
            job="urge_scan",
            retry_of_job_run_id=RUN_ID,
            idempotency_key=KEY,
            payload_hash="b" * 64,
            actor=actor(),
            now=NOW,
        )
    assert caught.value.code == code


async def test_retry_of_failed_run_is_queued_once() -> None:
    session = FakeSession(scalars=[True, None, run(), None])
    result = await repository(session).enqueue_trigger(
        job="urge_scan",
        retry_of_job_run_id=RUN_ID,
        idempotency_key=KEY,
        payload_hash="b" * 64,
        actor=actor(),
        now=NOW,
    )
    assert result.retry_of_job_run_id == RUN_ID
    stored = next(value for value in session.added if isinstance(value, JobTriggerRequest))
    assert stored.retry_of_job_run_id == RUN_ID
