from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from sd_agent.rules import (
    CalendarCoverageError,
    TaskRuleInput,
    TaskStatus,
    UrgeLevel,
    WeightedTask,
    evaluate_task,
    make_urge_decision,
    weighted_progress,
)

AS_OF = date(2026, 7, 20)
WORKDAYS = frozenset(AS_OF + timedelta(days=offset) for offset in range(-40, 41))


@pytest.mark.parametrize(
    ("offset", "progress", "exempt_offset", "expected"),
    [
        *((offset, 100, None, TaskStatus.COMPLETED) for offset in range(-10, 11)),
        *((offset, 30, 2, TaskStatus.EXEMPT) for offset in range(-10, 11)),
        *((offset, 30, None, TaskStatus.OVERDUE) for offset in range(-10, 0)),
        *((offset, 30, None, TaskStatus.DUE_SOON) for offset in range(0, 6)),
        *((offset, 30, None, TaskStatus.ON_TRACK) for offset in range(6, 11)),
    ],
)
def test_status_matrix(
    offset: int,
    progress: int,
    exempt_offset: int | None,
    expected: TaskStatus,
) -> None:
    task = TaskRuleInput(
        task_id=offset + 100,
        deadline=AS_OF + timedelta(days=offset),
        progress=progress,
        exempt_until=(AS_OF + timedelta(days=exempt_offset) if exempt_offset is not None else None),
    )
    assert evaluate_task(task, as_of=AS_OF, workdays=WORKDAYS).status is expected


@pytest.mark.parametrize("progress", [-1, 101])
def test_task_rejects_invalid_progress(progress: int) -> None:
    with pytest.raises(ValueError, match="progress"):
        TaskRuleInput(task_id=1, deadline=AS_OF, progress=progress)


def test_evaluation_requires_authoritative_calendar_endpoints() -> None:
    task = TaskRuleInput(task_id=1, deadline=AS_OF + timedelta(days=2), progress=10)
    with pytest.raises(CalendarCoverageError):
        evaluate_task(task, as_of=AS_OF, workdays=frozenset({AS_OF}))


def test_evaluation_rejects_negative_threshold() -> None:
    task = TaskRuleInput(task_id=1, deadline=AS_OF, progress=10)
    with pytest.raises(ValueError, match="due_soon"):
        evaluate_task(task, as_of=AS_OF, workdays=WORKDAYS, due_soon_workdays=-1)


@pytest.mark.parametrize(
    ("offset", "progress", "exempt", "level", "eligible"),
    [
        (10, 10, None, UrgeLevel.NONE, False),
        (1, 100, None, UrgeLevel.NONE, False),
        (1, 10, 1, UrgeLevel.NONE, False),
        (1, 10, None, UrgeLevel.REMINDER, True),
        (-1, 10, None, UrgeLevel.OVERDUE, True),
        (-3, 10, None, UrgeLevel.ESCALATED, True),
        (-10, 10, None, UrgeLevel.ESCALATED, True),
    ],
)
def test_urge_levels(
    offset: int,
    progress: int,
    exempt: int | None,
    level: UrgeLevel,
    eligible: bool,
) -> None:
    task = TaskRuleInput(
        task_id=8,
        deadline=AS_OF + timedelta(days=offset),
        progress=progress,
        exempt_until=AS_OF + timedelta(days=exempt) if exempt is not None else None,
    )
    decision = make_urge_decision(
        task,
        target_id=9,
        as_of=AS_OF,
        workdays=WORKDAYS,
    )
    assert decision.level is level
    assert decision.eligible is eligible


def test_urge_dedup_is_deterministic() -> None:
    task = TaskRuleInput(task_id=8, deadline=AS_OF, progress=20)
    first = make_urge_decision(task, target_id=9, as_of=AS_OF, workdays=WORKDAYS)
    assert first.dedup_key is not None
    duplicate = make_urge_decision(
        task,
        target_id=9,
        as_of=AS_OF,
        workdays=WORKDAYS,
        existing_dedup_keys=frozenset({first.dedup_key}),
    )
    assert duplicate.eligible is False
    assert duplicate.reason == "duplicate"


def test_urge_rejects_invalid_escalation_threshold() -> None:
    task = TaskRuleInput(task_id=8, deadline=AS_OF, progress=20)
    with pytest.raises(ValueError, match="escalate_after"):
        make_urge_decision(
            task,
            target_id=9,
            as_of=AS_OF,
            workdays=WORKDAYS,
            escalate_after_workdays=0,
        )


@pytest.mark.parametrize(
    ("tasks", "expected"),
    [
        ((), None),
        ((WeightedTask(50, Decimal("1"), exempt=True),), None),
        ((WeightedTask(50, Decimal("1")),), Decimal("50.00")),
        (
            (
                WeightedTask(20, Decimal("1")),
                WeightedTask(80, Decimal("3")),
            ),
            Decimal("65.00"),
        ),
        (
            (
                WeightedTask(33, Decimal("1")),
                WeightedTask(34, Decimal("1")),
            ),
            Decimal("33.50"),
        ),
    ],
)
def test_weighted_progress(
    tasks: tuple[WeightedTask, ...],
    expected: Decimal | None,
) -> None:
    assert weighted_progress(tasks) == expected


@pytest.mark.parametrize(
    ("progress", "weight"),
    [(-1, Decimal("1")), (101, Decimal("1")), (10, Decimal("0")), (10, Decimal("101"))],
)
def test_weighted_task_validation(progress: int, weight: Decimal) -> None:
    with pytest.raises(ValueError):
        WeightedTask(progress, weight)
