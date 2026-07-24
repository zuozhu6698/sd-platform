from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest

from sd_agent.adapters.teable import TeableAdapterError, TeableRecord
from sd_agent.jobs import SqlUrgeCommandSink, TeableUrgeSource, UrgeCommand
from sd_agent.jobs import urge as urge_module
from sd_agent.persistence.models import AuditEvent
from sd_agent.rules import UrgeLevel

NOW = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)


class FakeTeable:
    def __init__(self) -> None:
        self.records: dict[str, list[TeableRecord]] = {
            "task": [
                TeableRecord(
                    id="rec-task",
                    fields={
                        "task_id": 101,
                        "content": "重点任务",
                        "deadline": "2026-07-30",
                        "progress": 50,
                        "status": "active",
                        "exempt_until": None,
                    },
                ),
                TeableRecord(
                    id="rec-done",
                    fields={
                        "task_id": 102,
                        "content": "已完成",
                        "deadline": "2026-07-30",
                        "progress": 100,
                        "status": "completed",
                    },
                ),
            ],
            "task_owner": [
                TeableRecord(
                    id="rec-owner",
                    fields={
                        "task_id": 101,
                        "person_id": 7,
                        "owner_type": "primary",
                        "active": True,
                    },
                ),
                TeableRecord(
                    id="rec-secondary",
                    fields={
                        "task_id": 101,
                        "person_id": 8,
                        "owner_type": "secondary",
                        "active": True,
                    },
                ),
            ],
            "work_calendar": [
                TeableRecord(
                    id="rec-calendar",
                    fields={"calendar_date": "2026-07-24", "is_workday": True},
                )
            ],
            "urge_log": [TeableRecord(id="rec-urge", fields={"dedup_key": "urge:v1:old"})],
        }
        self.calls: list[tuple[str, tuple[str, ...], int, int]] = []

    async def list_records(
        self,
        table: str,
        *,
        projection: tuple[str, ...],
        take: int,
        skip: int,
    ) -> list[TeableRecord]:
        self.calls.append((table, projection, take, skip))
        return self.records[table][skip : skip + take]


async def test_teable_urge_source_filters_status_and_owner_type() -> None:
    teable = FakeTeable()
    snapshot = await TeableUrgeSource(teable).load(as_of=date(2026, 7, 24))  # type: ignore[arg-type]
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0].task_id == 101
    assert snapshot.tasks[0].target_ids == (7,)
    assert snapshot.workdays == frozenset({date(2026, 7, 24)})
    assert snapshot.existing_dedup_keys == frozenset({"urge:v1:old"})
    assert {call[0] for call in teable.calls} == {
        "task",
        "task_owner",
        "work_calendar",
        "urge_log",
    }


async def test_teable_urge_source_rejects_invalid_fields() -> None:
    teable = FakeTeable()
    teable.records["task"][0].fields["deadline"] = "not-a-date"
    with pytest.raises(TeableAdapterError) as captured:
        await TeableUrgeSource(teable).load(as_of=date(2026, 7, 24))  # type: ignore[arg-type]
    assert captured.value.code == "TEABLE_INVALID_RESPONSE"
    assert captured.value.retryable is False


@pytest.mark.parametrize("conflict", ["missing_owner", "multiple_owner", "duplicate_task"])
async def test_teable_urge_source_fails_closed_for_domain_conflicts(conflict: str) -> None:
    teable = FakeTeable()
    if conflict == "missing_owner":
        teable.records["task_owner"] = []
    elif conflict == "multiple_owner":
        teable.records["task_owner"].append(
            TeableRecord(
                id="rec-owner-2",
                fields={
                    "task_id": 101,
                    "person_id": 8,
                    "owner_type": "primary",
                    "active": True,
                },
            )
        )
    else:
        teable.records["task"].append(teable.records["task"][0].model_copy())
    with pytest.raises(TeableAdapterError, match="TEABLE_(OWNER|TASK)_CONFLICT"):
        await TeableUrgeSource(teable).load(as_of=date(2026, 7, 24))  # type: ignore[arg-type]


async def test_urge_source_paginates_without_truncation() -> None:
    class PagedTeable:
        def __init__(self) -> None:
            self.skips: list[int] = []

        async def list_records(
            self,
            table: str,
            *,
            projection: tuple[str, ...],
            take: int,
            skip: int,
        ) -> list[TeableRecord]:
            self.skips.append(skip)
            if skip == 0:
                return [TeableRecord(id=f"rec-{index}", fields={}) for index in range(1000)]
            return []

    teable = PagedTeable()
    records = await urge_module._all_records(  # noqa: SLF001 -- pagination contract unit
        teable,  # type: ignore[arg-type]
        "task",
        ("task_id",),
    )
    assert len(records) == 1000 and teable.skips == [0, 1000]


class FakeSession:
    def __init__(self, values: list[str | None]) -> None:
        self.values = values
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> str | None:
        return self.values.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)


class FakeSessions:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self.session


def sink(session: FakeSession) -> SqlUrgeCommandSink:
    value = object.__new__(SqlUrgeCommandSink)
    value._sessions = FakeSessions(session)  # type: ignore[assignment]
    return value


def command(dedup_key: str, task_id: int) -> UrgeCommand:
    return UrgeCommand(
        task_id,
        7,
        UrgeLevel.REMINDER,
        "安全的固定催办内容",
        dedup_key,
        NOW,
    )


def test_sql_urge_sink_builds_non_expiring_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, bool]] = []

    def factory(engine: object, *, expire_on_commit: bool) -> object:
        seen.append((engine, expire_on_commit))
        return object()

    monkeypatch.setattr(urge_module, "async_sessionmaker", factory)
    engine = object()
    SqlUrgeCommandSink(engine)  # type: ignore[arg-type]
    assert seen == [(engine, False)]


async def test_sql_urge_sink_inserts_unique_outbox_and_transactional_audit() -> None:
    session = FakeSession(["outbox-1", None])
    inserted = await sink(session).enqueue(
        (command("urge:v1:101:7:2026-07-24:reminder", 101), command("duplicate", 102))
    )
    assert inserted == 1
    audit = next(value for value in session.added if isinstance(value, AuditEvent))
    assert audit.what == "urge.plan" and audit.target_id == "101"
    assert audit.details["outbox_id"] == "outbox-1"
    assert "content" not in audit.details
