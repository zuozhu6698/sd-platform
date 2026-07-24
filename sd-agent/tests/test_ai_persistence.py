from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from sd_agent.ai import AiRunRecord
from sd_agent.persistence import ai as ai_module
from sd_agent.persistence.ai import SqlAiRunRepository
from sd_agent.persistence.models import AiRun


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def repository(session: FakeSession) -> SqlAiRunRepository:
    instance = object.__new__(SqlAiRunRepository)
    instance._sessions = FakeSessions(session)  # type: ignore[assignment]
    return instance


def test_repository_builds_non_expiring_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(ai_module, "async_sessionmaker", factory)
    engine = object()
    SqlAiRunRepository(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_record_persists_safe_ai_provenance() -> None:
    session = FakeSession()
    record = AiRunRecord(
        "review",
        "a" * 64,
        ("L2", "T1"),
        "b8-v1",
        "offline-deterministic-v1",
        {"temperature": 0},
        {"result": "通过"},
        True,
        datetime(2026, 7, 24, tzinfo=UTC),
    )

    ai_run_id = await repository(session).record(record)

    row = session.added[0]
    assert isinstance(row, AiRun)
    assert row.ai_run_id == ai_run_id
    assert row.source_ids == ["L2", "T1"]
    assert row.output == {"result": "通过"}
    assert row.reviewed_by is None
