from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sd_agent.adapters.teable import TeableAdapterError, TeableFilter, TeableRecord
from sd_agent.tasks import MyTasksService


class FakeTeable:
    def __init__(self) -> None:
        self.owners: list[TeableRecord] = []
        self.tasks: dict[int, list[TeableRecord]] = {}

    async def list_records(
        self,
        table: str,
        *,
        filter_by: TeableFilter,
        **_kwargs: Any,
    ) -> list[TeableRecord]:
        if table == "task_owner":
            return self.owners
        return self.tasks.get(int(filter_by.value), [])


def service(teable: FakeTeable) -> MyTasksService:
    return MyTasksService(teable)  # type: ignore[arg-type]


def owner(
    task_id: int,
    *,
    person_id: int = 7,
    kind: str = "primary",
    active: bool = True,
) -> TeableRecord:
    return TeableRecord(
        id=f"owner-{task_id}",
        fields={
            "task_id": task_id,
            "person_id": person_id,
            "owner_type": kind,
            "active": active,
        },
    )


def task(
    task_id: int,
    *,
    deadline: str | None,
    progress: int = 50,
    status: str = "active",
    ai_flag: str | None = None,
) -> TeableRecord:
    return TeableRecord(
        id=f"task-{task_id}",
        fields={
            "task_id": task_id,
            "kw_id": 3,
            "unit_id": 10,
            "category": "重点",
            "content": f"事项 {task_id}",
            "deadline": deadline,
            "progress": progress,
            "status": status,
            "revision": 4,
            "ai_flag": ai_flag,
        },
    )


async def test_lists_only_active_primary_ownership_and_sorts_deadlines() -> None:
    teable = FakeTeable()
    teable.owners = [
        owner(3),
        owner(1),
        owner(2, kind="collaborator"),
        owner(4, active=False),
        owner(5, person_id=8),
        owner(3),
    ]
    teable.tasks = {
        1: [task(1, deadline=None, ai_flag="risk")],
        3: [task(3, deadline="2026-07-20")],
    }
    result = await service(teable).list_for_person(7, today=date(2026, 7, 23))
    assert [item.task_id for item in result.tasks] == [3, 1]
    assert (result.total, result.overdue, result.attention) == (2, 1, 1)


@pytest.mark.parametrize(
    ("progress", "status"),
    [(100, "completed"), (50, "paused")],
)
async def test_completed_or_paused_task_is_not_counted_overdue(
    progress: int,
    status: str,
) -> None:
    teable = FakeTeable()
    teable.owners = [owner(1)]
    teable.tasks = {1: [task(1, deadline="2026-07-20", progress=progress, status=status)]}
    result = await service(teable).list_for_person(7, today=date(2026, 7, 23))
    assert result.overdue == 0


async def test_missing_duplicate_or_mismatched_task_fails_closed() -> None:
    teable = FakeTeable()
    teable.owners = [owner(1)]
    with pytest.raises(TeableAdapterError, match="TEABLE_TASK_CONFLICT"):
        await service(teable).list_for_person(7, today=date(2026, 7, 23))
    teable.tasks = {1: [task(1, deadline=None), task(1, deadline=None)]}
    with pytest.raises(TeableAdapterError, match="TEABLE_TASK_CONFLICT"):
        await service(teable).list_for_person(7, today=date(2026, 7, 23))
    teable.tasks = {1: [task(2, deadline=None)]}
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await service(teable).list_for_person(7, today=date(2026, 7, 23))


async def test_invalid_owner_or_task_payload_and_person_id_are_rejected() -> None:
    teable = FakeTeable()
    with pytest.raises(ValueError, match="person_id"):
        await service(teable).list_for_person(0, today=date(2026, 7, 23))
    teable.owners = [TeableRecord(id="bad", fields={"task_id": "bad"})]
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await service(teable).list_for_person(7, today=date(2026, 7, 23))
    teable.owners = [owner(1)]
    teable.tasks = {1: [TeableRecord(id="bad", fields={"task_id": 1})]}
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await service(teable).list_for_person(7, today=date(2026, 7, 23))
