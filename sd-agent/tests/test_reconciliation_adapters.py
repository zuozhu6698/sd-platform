from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from sd_agent.adapters.teable import TeableAdapterError, TeableRecord
from sd_agent.jobs import (
    ProgressRecovery,
    ReconciliationCandidate,
    SqlReconciliationRepository,
    TeableReconciliationGateway,
)
from sd_agent.jobs import reconciliation as module
from sd_agent.persistence.models import AuditEvent, OutboxMessage, SubmissionCommand
from sd_agent.submission import TaskSnapshot

NOW = datetime(2026, 7, 24, 2, 5, tzinfo=UTC)


class FakeTeable:
    def __init__(self, records: list[TeableRecord]) -> None:
        self.records = records
        self.calls: list[tuple[str, tuple[str, ...], object, int]] = []

    async def list_records(
        self,
        table: str,
        *,
        projection: tuple[str, ...],
        filter_by: object,
        take: int,
    ) -> list[TeableRecord]:
        self.calls.append((table, projection, filter_by, take))
        return self.records


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.task = TaskSnapshot("rec-task", 101, 10, 7, 50, 4)
        self.updated: tuple[int, int, str] | None = None

    async def get_task(self, task_id: int) -> TaskSnapshot | None:
        return self.task if task_id == self.task.task_id else None

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot:
        self.updated = (progress, next_revision, idempotency_key)
        return TaskSnapshot(
            task.record_id,
            task.task_id,
            task.unit_id,
            task.primary_owner_id,
            progress,
            next_revision,
        )


def recovery_record(**overrides: object) -> TeableRecord:
    fields: dict[str, object] = {
        "log_id": 9001,
        "command_id": "command-1",
        "task_id": 101,
        "reporter_id": 7,
        "progress": 65,
        "attachments": ["file_bbbbbbbb", "file_aaaaaaaa", "file_bbbbbbbb"],
    }
    fields.update(overrides)
    return TeableRecord(id="rec-log", fields=fields)


async def test_teable_reconciliation_gateway_reads_and_normalizes_recovery() -> None:
    teable = FakeTeable([recovery_record()])
    submission = FakeSubmissionGateway()
    gateway = TeableReconciliationGateway(
        teable=teable,  # type: ignore[arg-type]
        submission=submission,  # type: ignore[arg-type]
    )
    recovery = await gateway.find_recovery("command-1")
    assert recovery == ProgressRecovery(
        "rec-log",
        9001,
        "command-1",
        101,
        7,
        65,
        ("file_aaaaaaaa", "file_bbbbbbbb"),
    )
    assert await gateway.get_task(101) == submission.task
    updated = await gateway.update_task_progress(
        submission.task,
        progress=65,
        next_revision=5,
        idempotency_key="reconcile:command-1",
    )
    assert updated.progress == 65 and submission.updated == (65, 5, "reconcile:command-1")


async def test_teable_reconciliation_gateway_handles_missing_conflict_and_invalid() -> None:
    submission = FakeSubmissionGateway()
    assert (
        await TeableReconciliationGateway(
            teable=FakeTeable([]),  # type: ignore[arg-type]
            submission=submission,  # type: ignore[arg-type]
        ).find_recovery("command-1")
        is None
    )
    with pytest.raises(TeableAdapterError, match="TEABLE_PROGRESS_CONFLICT"):
        await TeableReconciliationGateway(
            teable=FakeTeable([recovery_record(), recovery_record()]),  # type: ignore[arg-type]
            submission=submission,  # type: ignore[arg-type]
        ).find_recovery("command-1")
    for bad in (
        recovery_record(command_id="other"),
        recovery_record(progress=101),
        recovery_record(attachments=[""]),
        recovery_record(attachments=["unsafe-file-id"]),
    ):
        with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
            await TeableReconciliationGateway(
                teable=FakeTeable([bad]),  # type: ignore[arg-type]
                submission=submission,  # type: ignore[arg-type]
            ).find_recovery("command-1")


def command_row(**overrides: object) -> SubmissionCommand:
    values: dict[str, object] = {
        "command_id": "command-1",
        "idempotency_key": "idem-1",
        "person_id": 7,
        "task_id": 101,
        "payload_hash": "a" * 64,
        "state": "teable_written",
        "teable_record_id": "rec-log",
        "result": None,
        "last_error_code": None,
        "created_at": NOW - timedelta(minutes=10),
        "updated_at": NOW - timedelta(minutes=10),
    }
    values.update(overrides)
    return SubmissionCommand(**values)  # type: ignore[arg-type]


class FakeSession:
    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        scalar_rows: list[object] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.scalar_rows = list(scalar_rows or [])
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _statement: object) -> list[object]:
        return self.scalar_rows

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


def repository(session: FakeSession) -> SqlReconciliationRepository:
    value = object.__new__(SqlReconciliationRepository)
    value._sessions = FakeSessions(session)  # type: ignore[assignment]
    return value


def test_sql_reconciliation_repository_builds_non_expiring_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(module, "async_sessionmaker", factory)
    engine = object()
    SqlReconciliationRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_sql_reconciliation_lists_and_claims_current_candidate() -> None:
    row = command_row()
    session = FakeSession(scalar_values=[True, row], scalar_rows=[row])
    repo = repository(session)
    candidates = await repo.list_candidates(before=NOW, limit=100)
    assert candidates == (
        ReconciliationCandidate("command-1", 7, 101, NOW - timedelta(minutes=10)),
    )
    async with repo.claim(candidates[0], before=NOW) as claim:
        assert claim is not None and claim.candidate == candidates[0]


@pytest.mark.parametrize("values", [[False], [True, None]])
async def test_sql_reconciliation_skips_lock_or_stale_row(values: list[object | None]) -> None:
    repo = repository(FakeSession(scalar_values=values))
    async with repo.claim(ReconciliationCandidate("command-1", 7, 101, NOW), before=NOW) as claim:
        assert claim is None


async def test_sql_claim_completes_command_audit_outbox_and_file_binding() -> None:
    row = command_row()
    session = FakeSession(scalar_values=[True, row], scalar_rows=["file_aaaaaaaa"])
    repo = repository(session)
    candidate = ReconciliationCandidate("command-1", 7, 101, row.updated_at)
    recovery = ProgressRecovery("rec-log", 9001, "command-1", 101, 7, 65, ("file_aaaaaaaa",))
    async with repo.claim(candidate, before=NOW) as claim:
        assert claim is not None
        await claim.complete(recovery, now=NOW)
    assert row.state == "committed" and row.result["log_id"] == 9001
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    outbox = next(value for value in session.added if isinstance(value, OutboxMessage))
    assert audit.what == "report.reconcile" and audit.details["command_id"] == "command-1"
    assert outbox.dedup_key == "submission:command-1:oa.complete_pending"

    no_files_row = command_row(command_id="command-2")
    no_files_session = FakeSession(scalar_values=[True, no_files_row])
    no_files_repo = repository(no_files_session)
    no_files_candidate = ReconciliationCandidate("command-2", 7, 101, no_files_row.updated_at)
    async with no_files_repo.claim(no_files_candidate, before=NOW) as claim:
        assert claim is not None
        await claim.complete(
            ProgressRecovery("rec-2", 9002, "command-2", 101, 7, 80, ()),
            now=NOW,
        )
    assert no_files_row.state == "committed"


async def test_sql_claim_fails_closed_when_file_binding_changes() -> None:
    row = command_row()
    session = FakeSession(scalar_values=[True, row], scalar_rows=[])
    repo = repository(session)
    candidate = ReconciliationCandidate("command-1", 7, 101, row.updated_at)
    recovery = ProgressRecovery("rec-log", 9001, "command-1", 101, 7, 65, ("file_aaaaaaaa",))
    async with repo.claim(candidate, before=NOW) as claim:
        assert claim is not None
        with pytest.raises(RuntimeError, match="RECONCILIATION_FILE_BINDING_CHANGED"):
            await claim.complete(recovery, now=NOW)
