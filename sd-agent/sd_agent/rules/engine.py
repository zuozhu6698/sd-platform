from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class CalendarCoverageError(ValueError):
    """Raised when an authoritative work-calendar range is incomplete."""


class TaskStatus(StrEnum):
    COMPLETED = "completed"
    EXEMPT = "exempt"
    OVERDUE = "overdue"
    DUE_SOON = "due_soon"
    ON_TRACK = "on_track"


class UrgeLevel(StrEnum):
    NONE = "none"
    REMINDER = "reminder"
    OVERDUE = "overdue"
    ESCALATED = "escalated"


@dataclass(frozen=True, slots=True)
class TaskRuleInput:
    task_id: int
    deadline: date
    progress: int
    exempt_until: date | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class TaskEvaluation:
    status: TaskStatus
    workdays_to_deadline: int


@dataclass(frozen=True, slots=True)
class UrgeDecision:
    eligible: bool
    level: UrgeLevel
    dedup_key: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class WeightedTask:
    progress: int
    weight: Decimal
    exempt: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        if not Decimal("0.1") <= self.weight <= Decimal("100"):
            raise ValueError("weight must be between 0.1 and 100")


def _workday_distance(as_of: date, deadline: date, workdays: frozenset[date]) -> int:
    low, high = sorted((as_of, deadline))
    if low not in workdays or high not in workdays:
        raise CalendarCoverageError("calendar must include as_of and deadline")
    between = sum(1 for day in workdays if low < day <= high)
    return between if deadline >= as_of else -between


def evaluate_task(
    task: TaskRuleInput,
    *,
    as_of: date,
    workdays: frozenset[date],
    due_soon_workdays: int = 5,
) -> TaskEvaluation:
    if due_soon_workdays < 0:
        raise ValueError("due_soon_workdays must not be negative")
    distance = _workday_distance(as_of, task.deadline, workdays)
    if task.progress == 100:
        return TaskEvaluation(TaskStatus.COMPLETED, distance)
    if task.exempt_until is not None and task.exempt_until >= as_of:
        return TaskEvaluation(TaskStatus.EXEMPT, distance)
    if task.deadline < as_of:
        return TaskEvaluation(TaskStatus.OVERDUE, distance)
    if distance <= due_soon_workdays:
        return TaskEvaluation(TaskStatus.DUE_SOON, distance)
    return TaskEvaluation(TaskStatus.ON_TRACK, distance)


def make_urge_decision(
    task: TaskRuleInput,
    *,
    target_id: int,
    as_of: date,
    workdays: frozenset[date],
    existing_dedup_keys: frozenset[str] = frozenset(),
    rule_version: str = "v1",
    due_soon_workdays: int = 5,
    escalate_after_workdays: int = 3,
) -> UrgeDecision:
    if escalate_after_workdays < 1:
        raise ValueError("escalate_after_workdays must be positive")
    evaluation = evaluate_task(
        task,
        as_of=as_of,
        workdays=workdays,
        due_soon_workdays=due_soon_workdays,
    )
    if evaluation.status in {TaskStatus.COMPLETED, TaskStatus.EXEMPT, TaskStatus.ON_TRACK}:
        return UrgeDecision(False, UrgeLevel.NONE, None, evaluation.status.value)

    overdue_workdays = max(0, -evaluation.workdays_to_deadline)
    if overdue_workdays >= escalate_after_workdays:
        level = UrgeLevel.ESCALATED
    elif evaluation.status is TaskStatus.OVERDUE:
        level = UrgeLevel.OVERDUE
    else:
        level = UrgeLevel.REMINDER
    dedup_key = f"urge:{rule_version}:{task.task_id}:{target_id}:{as_of.isoformat()}:{level.value}"
    if dedup_key in existing_dedup_keys:
        return UrgeDecision(False, level, dedup_key, "duplicate")
    return UrgeDecision(True, level, dedup_key, "eligible")


def weighted_progress(tasks: tuple[WeightedTask, ...]) -> Decimal | None:
    active = tuple(task for task in tasks if not task.exempt)
    if not active:
        return None
    total_weight = sum((task.weight for task in active), start=Decimal(0))
    weighted_sum = sum(
        (Decimal(task.progress) * task.weight for task in active),
        start=Decimal(0),
    )
    return (weighted_sum / total_weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
