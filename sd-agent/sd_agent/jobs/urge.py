from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableRecord
from sd_agent.persistence.models import AuditEvent, OutboxMessage
from sd_agent.rules import TaskRuleInput, UrgeLevel, make_urge_decision
from sd_agent.scheduler.service import JobOutcome


@dataclass(frozen=True, slots=True)
class UrgeTask:
    task_id: int
    content: str
    deadline: date
    progress: int
    exempt_until: date | None
    target_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UrgeSnapshot:
    tasks: tuple[UrgeTask, ...]
    calendar_dates: frozenset[date]
    workdays: frozenset[date]
    existing_dedup_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class UrgeCommand:
    task_id: int
    target_id: int
    level: UrgeLevel
    content: str
    dedup_key: str
    planned_at: datetime


class UrgeSource(Protocol):
    async def load(self, *, as_of: date) -> UrgeSnapshot: ...


class UrgeCommandSink(Protocol):
    async def enqueue(self, commands: tuple[UrgeCommand, ...]) -> int: ...


class UrgeScanHandler:
    def __init__(
        self,
        *,
        source: UrgeSource,
        sink: UrgeCommandSink,
        rule_version: str = "v1",
        due_soon_workdays: int = 5,
        escalate_after_workdays: int = 3,
    ) -> None:
        if not rule_version or len(rule_version) > 32:
            raise ValueError("rule_version must contain 1-32 characters")
        self._source = source
        self._sink = sink
        self._rule_version = rule_version
        self._due_soon_workdays = due_soon_workdays
        self._escalate_after_workdays = escalate_after_workdays

    async def __call__(self, scheduled_for: datetime) -> JobOutcome:
        as_of = scheduled_for.date()
        snapshot = await self._source.load(as_of=as_of)
        _require_calendar_coverage(snapshot, as_of=as_of)
        commands: list[UrgeCommand] = []
        evaluated = 0
        for task in snapshot.tasks:
            for target_id in task.target_ids:
                evaluated += 1
                decision = make_urge_decision(
                    TaskRuleInput(
                        task_id=task.task_id,
                        deadline=task.deadline,
                        progress=task.progress,
                        exempt_until=task.exempt_until,
                    ),
                    target_id=target_id,
                    as_of=as_of,
                    workdays=snapshot.workdays,
                    existing_dedup_keys=snapshot.existing_dedup_keys,
                    rule_version=self._rule_version,
                    due_soon_workdays=self._due_soon_workdays,
                    escalate_after_workdays=self._escalate_after_workdays,
                )
                if decision.eligible and decision.dedup_key is not None:
                    commands.append(
                        UrgeCommand(
                            task.task_id,
                            target_id,
                            decision.level,
                            _render_content(task, decision.level),
                            decision.dedup_key,
                            scheduled_for,
                        )
                    )
        inserted = await self._sink.enqueue(tuple(commands))
        return JobOutcome(
            {
                "tasks": len(snapshot.tasks),
                "evaluated": evaluated,
                "eligible": len(commands),
                "enqueued": inserted,
                "duplicates": len(commands) - inserted,
            }
        )


class _TaskFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    content: str
    deadline: date
    progress: int
    status: str
    exempt_until: date | None = None


class _OwnerFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    person_id: int
    owner_type: str
    active: bool


class _CalendarFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calendar_date: date
    is_workday: bool


class _UrgeFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dedup_key: str


class TeableUrgeSource:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def load(self, *, as_of: date) -> UrgeSnapshot:
        task_records = await _all_records(
            self._teable,
            "task",
            ("task_id", "content", "deadline", "progress", "status", "exempt_until"),
        )
        owner_records = await _all_records(
            self._teable,
            "task_owner",
            ("task_id", "person_id", "owner_type", "active"),
        )
        calendar_records = await _all_records(
            self._teable,
            "work_calendar",
            ("calendar_date", "is_workday"),
        )
        urge_records = await _all_records(self._teable, "urge_log", ("dedup_key",))
        tasks = [_validated(_TaskFields, record.fields) for record in task_records]
        owners = [_validated(_OwnerFields, record.fields) for record in owner_records]
        calendars = [_validated(_CalendarFields, record.fields) for record in calendar_records]
        urges = [_validated(_UrgeFields, record.fields) for record in urge_records]
        targets: dict[int, set[int]] = {}
        for owner in owners:
            if owner.active and owner.owner_type == "primary":
                targets.setdefault(owner.task_id, set()).add(owner.person_id)
        governed_tasks = [task for task in tasks if task.status not in {"completed", "cancelled"}]
        task_ids = [task.task_id for task in governed_tasks]
        if len(task_ids) != len(set(task_ids)):
            raise TeableAdapterError("TEABLE_TASK_CONFLICT", retryable=False)
        if any(len(targets.get(task.task_id, set())) != 1 for task in governed_tasks):
            raise TeableAdapterError("TEABLE_OWNER_CONFLICT", retryable=False)
        active_tasks = tuple(
            UrgeTask(
                task.task_id,
                task.content,
                task.deadline,
                task.progress,
                task.exempt_until,
                tuple(sorted(targets.get(task.task_id, set()))),
            )
            for task in governed_tasks
        )
        return UrgeSnapshot(
            active_tasks,
            frozenset(item.calendar_date for item in calendars),
            frozenset(item.calendar_date for item in calendars if item.is_workday),
            frozenset(item.dedup_key for item in urges),
        )


class SqlUrgeCommandSink:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def enqueue(self, commands: tuple[UrgeCommand, ...]) -> int:
        inserted = 0
        async with self._sessions.begin() as session:
            for command in commands:
                outbox_id = str(uuid4())
                result = await session.scalar(
                    insert(OutboxMessage)
                    .values(
                        outbox_id=outbox_id,
                        kind="oa.send_urge",
                        dedup_key=command.dedup_key,
                        payload={
                            "task_id": command.task_id,
                            "target_id": command.target_id,
                            "level": command.level.value,
                            "content": command.content,
                            "planned_at": command.planned_at.isoformat(),
                        },
                        state="pending",
                        available_at=command.planned_at,
                        lease_until=None,
                        attempt_count=0,
                        last_error_code=None,
                        created_at=command.planned_at,
                        updated_at=command.planned_at,
                    )
                    .on_conflict_do_nothing(index_elements=[OutboxMessage.dedup_key])
                    .returning(OutboxMessage.outbox_id)
                )
                if result is None:
                    continue
                inserted += 1
                session.add(
                    AuditEvent(
                        event_id=str(uuid4()),
                        request_id=f"job:{command.dedup_key}"[:64],
                        who="scheduler",
                        role=None,
                        scope={},
                        what="urge.plan",
                        target_type="task",
                        target_id=str(command.task_id),
                        result="success",
                        ip=None,
                        user_agent=None,
                        details={
                            "outbox_id": str(result),
                            "target_id": command.target_id,
                            "level": command.level.value,
                            "dedup_key": command.dedup_key,
                        },
                        created_at=command.planned_at,
                    )
                )
        return inserted


async def _all_records(
    teable: TeableClient,
    table: str,
    projection: tuple[str, ...],
) -> list[TeableRecord]:
    records: list[TeableRecord] = []
    skip = 0
    while True:
        page = await teable.list_records(table, projection=projection, take=1000, skip=skip)
        records.extend(page)
        if len(page) < 1000:
            return records
        skip += len(page)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _validated(model: type[ModelT], fields: dict[str, object]) -> ModelT:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc


def _require_calendar_coverage(snapshot: UrgeSnapshot, *, as_of: date) -> None:
    if not snapshot.tasks:
        return
    low = min(as_of, *(task.deadline for task in snapshot.tasks))
    high = max(as_of, *(task.deadline for task in snapshot.tasks))
    expected = frozenset(low + timedelta(days=offset) for offset in range((high - low).days + 1))
    if not expected <= snapshot.calendar_dates:
        raise RuntimeError("WORK_CALENDAR_INCOMPLETE")


def _render_content(task: UrgeTask, level: UrgeLevel) -> str:
    labels = {
        UrgeLevel.REMINDER: "临期提醒",
        UrgeLevel.OVERDUE: "逾期催办",
        UrgeLevel.ESCALATED: "逾期升级",
    }
    label = labels[level]
    safe_title = " ".join(task.content.split())[:80]
    return f"【{label}】事项“{safe_title}”截止日期为 {task.deadline.isoformat()}，请及时更新进展。"
