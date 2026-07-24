from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sd_agent.jobs import UrgeCommand, UrgeScanHandler, UrgeSnapshot, UrgeTask

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEDULED = datetime(2026, 7, 24, 9, 0, tzinfo=SHANGHAI)
AS_OF = SCHEDULED.date()


def calendar(start: date, end: date) -> frozenset[date]:
    return frozenset(start + timedelta(days=offset) for offset in range((end - start).days + 1))


class FakeSource:
    def __init__(self, snapshot: UrgeSnapshot) -> None:
        self.snapshot = snapshot
        self.as_of: date | None = None

    async def load(self, *, as_of: date) -> UrgeSnapshot:
        self.as_of = as_of
        return self.snapshot


class FakeSink:
    def __init__(self, inserted: int | None = None) -> None:
        self.commands: tuple[UrgeCommand, ...] = ()
        self.inserted = inserted

    async def enqueue(self, commands: tuple[UrgeCommand, ...]) -> int:
        self.commands = commands
        return len(commands) if self.inserted is None else self.inserted


def task(**overrides: object) -> UrgeTask:
    values: dict[str, object] = {
        "task_id": 101,
        "content": "完成重点项目联调",
        "deadline": AS_OF + timedelta(days=2),
        "progress": 50,
        "exempt_until": None,
        "target_ids": (7,),
    }
    values.update(overrides)
    return UrgeTask(**values)  # type: ignore[arg-type]


async def test_urge_handler_plans_deterministic_durable_commands() -> None:
    dates = calendar(AS_OF - timedelta(days=10), AS_OF + timedelta(days=10))
    source = FakeSource(UrgeSnapshot((task(),), dates, dates, frozenset()))
    sink = FakeSink()
    outcome = await UrgeScanHandler(source=source, sink=sink)(SCHEDULED)

    assert source.as_of == AS_OF
    assert outcome.counts == {
        "tasks": 1,
        "evaluated": 1,
        "eligible": 1,
        "enqueued": 1,
        "duplicates": 0,
    }
    assert len(sink.commands) == 1
    command = sink.commands[0]
    assert command.dedup_key == "urge:v1:101:7:2026-07-24:reminder"
    assert command.planned_at == SCHEDULED
    assert "临期提醒" in command.content and "2026-07-26" in command.content


async def test_urge_handler_skips_rule_duplicates_and_reports_db_race() -> None:
    dates = calendar(AS_OF - timedelta(days=10), AS_OF + timedelta(days=10))
    duplicate = "urge:v1:101:7:2026-07-24:reminder"
    snapshot = UrgeSnapshot(
        (task(), task(task_id=102, target_ids=(8, 9))),
        dates,
        dates,
        frozenset({duplicate}),
    )
    sink = FakeSink(inserted=1)
    outcome = await UrgeScanHandler(source=FakeSource(snapshot), sink=sink)(SCHEDULED)
    assert outcome.counts == {
        "tasks": 2,
        "evaluated": 3,
        "eligible": 2,
        "enqueued": 1,
        "duplicates": 1,
    }


async def test_urge_handler_fails_closed_for_incomplete_calendar() -> None:
    snapshot = UrgeSnapshot(
        (task(deadline=AS_OF + timedelta(days=2)),),
        frozenset({AS_OF, AS_OF + timedelta(days=2)}),
        frozenset({AS_OF, AS_OF + timedelta(days=2)}),
        frozenset(),
    )
    sink = FakeSink()
    with pytest.raises(RuntimeError, match="WORK_CALENDAR_INCOMPLETE"):
        await UrgeScanHandler(source=FakeSource(snapshot), sink=sink)(SCHEDULED)
    assert sink.commands == ()


async def test_urge_handler_allows_empty_snapshot_without_calendar() -> None:
    source = FakeSource(UrgeSnapshot((), frozenset(), frozenset(), frozenset()))
    outcome = await UrgeScanHandler(source=source, sink=FakeSink())(SCHEDULED)
    assert outcome.counts["tasks"] == 0


@pytest.mark.parametrize("version", ["", "x" * 33])
def test_urge_handler_rejects_invalid_rule_version(version: str) -> None:
    with pytest.raises(ValueError, match="rule_version"):
        UrgeScanHandler(
            source=FakeSource(UrgeSnapshot((), frozenset(), frozenset(), frozenset())),
            sink=FakeSink(),
            rule_version=version,
        )
