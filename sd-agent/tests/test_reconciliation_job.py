from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sd_agent.jobs import (
    ProgressRecovery,
    ReconciliationCandidate,
    ReconciliationHandler,
)
from sd_agent.submission import TaskSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 24, 10, 5, tzinfo=SHANGHAI)


class FakeClaim:
    def __init__(self, candidate: ReconciliationCandidate) -> None:
        self.candidate = candidate
        self.completed: ProgressRecovery | None = None

    async def complete(self, recovery: ProgressRecovery, *, now: datetime) -> None:
        assert now == NOW
        self.completed = recovery


class FakeRepository:
    def __init__(self, candidates: tuple[ReconciliationCandidate, ...]) -> None:
        self.candidates = candidates
        self.claims = {item.command_id: FakeClaim(item) for item in candidates}
        self.locked: set[str] = set()
        self.before: datetime | None = None

    async def list_candidates(
        self, *, before: datetime, limit: int
    ) -> tuple[ReconciliationCandidate, ...]:
        self.before = before
        assert limit == 100
        return self.candidates

    @asynccontextmanager
    async def claim(
        self, candidate: ReconciliationCandidate, *, before: datetime
    ) -> AsyncIterator[FakeClaim | None]:
        assert before == self.before
        yield None if candidate.command_id in self.locked else self.claims[candidate.command_id]


class FakeGateway:
    def __init__(self) -> None:
        self.recoveries: dict[str, ProgressRecovery | None] = {}
        self.tasks: dict[int, TaskSnapshot | None] = {}
        self.updates: list[tuple[int, int, str]] = []
        self.bad_update = False

    async def find_recovery(self, command_id: str) -> ProgressRecovery | None:
        return self.recoveries.get(command_id)

    async def get_task(self, task_id: int) -> TaskSnapshot | None:
        return self.tasks.get(task_id)

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot:
        self.updates.append((progress, next_revision, idempotency_key))
        if self.bad_update:
            return task
        updated = replace(task, progress=progress, revision=next_revision)
        self.tasks[task.task_id] = updated
        return updated


def candidate(command_id: str = "command-1") -> ReconciliationCandidate:
    return ReconciliationCandidate(command_id, 7, 101, NOW - timedelta(minutes=10))


def recovery(**overrides: object) -> ProgressRecovery:
    values: dict[str, object] = {
        "record_id": "rec-log",
        "log_id": 9001,
        "command_id": "command-1",
        "task_id": 101,
        "reporter_id": 7,
        "progress": 65,
        "file_ids": (),
    }
    values.update(overrides)
    return ProgressRecovery(**values)  # type: ignore[arg-type]


async def test_reconciliation_recovers_missing_task_update_and_commits() -> None:
    repo = FakeRepository((candidate(),))
    gateway = FakeGateway()
    gateway.recoveries["command-1"] = recovery()
    gateway.tasks[101] = TaskSnapshot("rec-task", 101, 10, 7, 50, 4)
    outcome = await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)
    assert repo.before == NOW - timedelta(minutes=5)
    assert gateway.updates == [(65, 5, "reconcile:command-1")]
    assert repo.claims["command-1"].completed == recovery()
    assert outcome.counts == {
        "candidates": 1,
        "locked": 0,
        "missing_progress": 0,
        "already_current": 0,
        "task_updated": 1,
        "recovered": 1,
    }


async def test_reconciliation_never_regresses_newer_progress() -> None:
    repo = FakeRepository((candidate(),))
    gateway = FakeGateway()
    gateway.recoveries["command-1"] = recovery(progress=65)
    gateway.tasks[101] = TaskSnapshot("rec-task", 101, 10, 7, 80, 8)
    outcome = await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)
    assert gateway.updates == []
    assert outcome.counts["already_current"] == 1
    assert outcome.counts["recovered"] == 1


async def test_reconciliation_counts_lock_and_missing_progress() -> None:
    candidates = (candidate("locked"), candidate("missing"))
    repo = FakeRepository(candidates)
    repo.locked.add("locked")
    gateway = FakeGateway()
    gateway.recoveries["missing"] = None
    outcome = await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)
    assert outcome.counts["locked"] == 1
    assert outcome.counts["missing_progress"] == 1
    assert outcome.counts["recovered"] == 0


@pytest.mark.parametrize(
    "bad_recovery",
    [
        recovery(command_id="other"),
        recovery(task_id=102),
        recovery(reporter_id=8),
    ],
)
async def test_reconciliation_rejects_identity_mismatch(
    bad_recovery: ProgressRecovery,
) -> None:
    repo = FakeRepository((candidate(),))
    gateway = FakeGateway()
    gateway.recoveries["command-1"] = bad_recovery
    with pytest.raises(RuntimeError, match="RECONCILIATION_IDENTITY_MISMATCH"):
        await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)


async def test_reconciliation_rejects_missing_task_or_unconfirmed_write() -> None:
    repo = FakeRepository((candidate(),))
    gateway = FakeGateway()
    gateway.recoveries["command-1"] = recovery()
    gateway.tasks[101] = None
    with pytest.raises(RuntimeError, match="RECONCILIATION_TASK_MISSING"):
        await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)

    gateway.tasks[101] = TaskSnapshot("rec-task", 101, 10, 7, 50, 4)
    gateway.bad_update = True
    with pytest.raises(RuntimeError, match="RECONCILIATION_WRITE_UNCONFIRMED"):
        await ReconciliationHandler(repository=repo, gateway=gateway)(NOW)


@pytest.mark.parametrize(
    ("stale_after", "batch_size"),
    [(timedelta(seconds=30), 100), (timedelta(minutes=5), 0), (timedelta(minutes=5), 501)],
)
def test_reconciliation_rejects_invalid_configuration(
    stale_after: timedelta, batch_size: int
) -> None:
    with pytest.raises(ValueError, match="reconciliation configuration"):
        ReconciliationHandler(
            repository=FakeRepository(()),
            gateway=FakeGateway(),
            stale_after=stale_after,
            batch_size=batch_size,
        )
