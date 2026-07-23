from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableFilter


class _OwnerFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    person_id: int
    owner_type: str
    active: bool


class _TaskFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    kw_id: int
    unit_id: int
    category: str
    content: str
    deadline: date | None = None
    progress: int
    status: str
    revision: int
    ai_flag: str | None = None


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class MyTask:
    task_id: int
    kw_id: int
    unit_id: int
    category: str
    content: str
    deadline: date | None
    progress: int
    status: str
    revision: int
    ai_flag: str | None


@dataclass(frozen=True, slots=True)
class MyTaskSummary:
    tasks: tuple[MyTask, ...]
    total: int
    overdue: int
    attention: int


class MyTasksService:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def list_for_person(self, person_id: int, *, today: date) -> MyTaskSummary:
        if person_id <= 0:
            raise ValueError("person_id must be positive")
        owners = await self._teable.list_records(
            "task_owner",
            projection=("task_id", "person_id", "owner_type", "active"),
            filter_by=TeableFilter("person_id", "is", person_id),
            take=1000,
        )
        task_ids: set[int] = set()
        for record in owners:
            owner = self._validate(_OwnerFields, record.fields)
            if owner.person_id == person_id and owner.owner_type == "primary" and owner.active:
                task_ids.add(owner.task_id)

        tasks: list[MyTask] = []
        for task_id in sorted(task_ids):
            records = await self._teable.list_records(
                "task",
                projection=(
                    "task_id",
                    "kw_id",
                    "unit_id",
                    "category",
                    "content",
                    "deadline",
                    "progress",
                    "status",
                    "revision",
                    "ai_flag",
                ),
                filter_by=TeableFilter("task_id", "is", task_id),
                take=2,
            )
            if len(records) != 1:
                raise TeableAdapterError("TEABLE_TASK_CONFLICT", retryable=False)
            fields = self._validate(_TaskFields, records[0].fields)
            if fields.task_id != task_id:
                raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False)
            tasks.append(MyTask(**fields.model_dump()))

        tasks.sort(
            key=lambda item: (item.deadline is None, item.deadline or date.max, item.task_id)
        )
        overdue = sum(
            task.deadline is not None
            and task.deadline < today
            and task.progress < 100
            and task.status not in {"completed", "paused"}
            for task in tasks
        )
        attention = sum(bool(task.ai_flag) for task in tasks)
        return MyTaskSummary(tuple(tasks), len(tasks), overdue, attention)

    @staticmethod
    def _validate(model: type[ModelT], fields: dict[str, object]) -> ModelT:
        try:
            return model.model_validate(fields)
        except ValidationError as exc:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
