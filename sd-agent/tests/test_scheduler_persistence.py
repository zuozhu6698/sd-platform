from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sd_agent.persistence import scheduler as scheduler_module
from sd_agent.persistence.scheduler import SqlJobRunRepository

NOW = datetime(2026, 7, 24, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class FakeResult:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class FakeSession:
    def __init__(self, *, locked: bool = True, results: list[str | None] | None = None) -> None:
        self.locked = locked
        self.results = list(results or [])
        self.executed: list[object] = []

    async def scalar(self, _statement: object) -> bool:
        return self.locked

    async def execute(self, statement: object) -> FakeResult:
        self.executed.append(statement)
        return FakeResult(self.results.pop(0))


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def repository(session: FakeSession) -> SqlJobRunRepository:
    instance = object.__new__(SqlJobRunRepository)
    instance._sessions = FakeSessions(session)  # type: ignore[assignment]
    return instance


def test_repository_builds_non_expiring_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(scheduler_module, "async_sessionmaker", factory)
    engine = object()
    SqlJobRunRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_claim_uses_advisory_lock_and_unique_insert() -> None:
    session = FakeSession(results=["11111111-1111-4111-8111-111111111111"])

    claim = await repository(session).claim_run(
        job="urge_scan",
        scheduled_for=NOW,
        config_hash="a" * 64,
        started_at=NOW,
    )

    assert claim is not None
    assert claim.job_run_id == "11111111-1111-4111-8111-111111111111"
    assert len(session.executed) == 1
    compiled = str(session.executed[0])
    assert "ON CONFLICT (job, scheduled_for) DO NOTHING" in compiled


@pytest.mark.parametrize(
    ("locked", "inserted"),
    [(False, "unused"), (True, None)],
)
async def test_claim_skips_competing_or_existing_run(locked: bool, inserted: str | None) -> None:
    session = FakeSession(locked=locked, results=[inserted] if locked else [])
    claim = await repository(session).claim_run(
        job="urge_scan",
        scheduled_for=NOW,
        config_hash="a" * 64,
        started_at=NOW,
    )
    assert claim is None


async def test_finish_updates_only_a_running_claim() -> None:
    session = FakeSession(results=["run-1"])
    counts: Mapping[str, int] = {"queued": 3}

    await repository(session).finish_run(
        job_run_id="run-1",
        state="succeeded",
        counts=counts,
        error_code=None,
        finished_at=NOW,
    )

    compiled = str(session.executed[0])
    assert "job_run.state =" in compiled
    assert "RETURNING sd_app.job_run.job_run_id" in compiled


async def test_finish_rejects_a_lost_or_already_finished_claim() -> None:
    with pytest.raises(RuntimeError, match="job run claim was lost"):
        await repository(FakeSession(results=[None])).finish_run(
            job_run_id="run-1",
            state="failed",
            counts={},
            error_code="JOB_EXECUTION_FAILED",
            finished_at=NOW,
        )
