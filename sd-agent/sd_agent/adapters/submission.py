from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableFilter
from sd_agent.persistence.models import FileObject
from sd_agent.submission import ProgressWrite, SubmissionInput, TaskSnapshot


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    unit_id: int
    progress: int
    revision: int


class OwnerFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    person_id: int
    owner_type: str
    active: bool


class ProgressFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    log_id: int
    command_id: str


ModelT = TypeVar("ModelT", bound=BaseModel)


class TeableSubmissionGateway:
    def __init__(self, *, teable: TeableClient, engine: AsyncEngine) -> None:
        self._teable = teable
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def get_task(self, task_id: int) -> TaskSnapshot | None:
        tasks = await self._teable.list_records(
            "task",
            projection=("task_id", "unit_id", "progress", "revision"),
            filter_by=TeableFilter("task_id", "is", task_id),
            take=2,
        )
        if not tasks:
            return None
        if len(tasks) != 1:
            raise TeableAdapterError("TEABLE_TASK_CONFLICT", retryable=False)
        task_fields = _validate(TaskFields, tasks[0].fields)
        owners = await self._teable.list_records(
            "task_owner",
            projection=("task_id", "person_id", "owner_type", "active"),
            filter_by=TeableFilter("task_id", "is", task_id),
            take=100,
        )
        primary_owners = [
            _validate(OwnerFields, owner.fields)
            for owner in owners
            if owner.fields.get("active") is True and owner.fields.get("owner_type") == "primary"
        ]
        if len(primary_owners) != 1 or primary_owners[0].task_id != task_id:
            raise TeableAdapterError("TEABLE_OWNER_CONFLICT", retryable=False)
        return TaskSnapshot(
            record_id=tasks[0].id,
            task_id=task_fields.task_id,
            unit_id=task_fields.unit_id,
            primary_owner_id=primary_owners[0].person_id,
            progress=task_fields.progress,
            revision=task_fields.revision,
        )

    async def files_are_clean(
        self,
        file_ids: tuple[str, ...],
        *,
        task_id: int,
        person_id: int,
    ) -> bool:
        if not file_ids:
            return True
        async with self._sessions() as session:
            rows = await session.scalars(
                select(FileObject.file_id).where(
                    FileObject.file_id.in_(file_ids),
                    FileObject.owner_person_id == person_id,
                    FileObject.state == "clean",
                    or_(FileObject.task_id.is_(None), FileObject.task_id == task_id),
                )
            )
        return set(rows) == set(file_ids)

    async def find_progress_by_command(self, command_id: str) -> ProgressWrite | None:
        records = await self._teable.list_records(
            "progress_log",
            projection=("log_id", "command_id"),
            filter_by=TeableFilter("command_id", "is", command_id),
            take=2,
        )
        if not records:
            return None
        if len(records) != 1:
            raise TeableAdapterError("TEABLE_PROGRESS_CONFLICT", retryable=False)
        fields = _validate(ProgressFields, records[0].fields)
        if fields.command_id != command_id:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False)
        return ProgressWrite(records[0].id, fields.log_id)

    async def append_progress(
        self,
        command_id: str,
        request: SubmissionInput,
        *,
        reporter_id: int,
        now: datetime,
    ) -> ProgressWrite:
        record = await self._teable.create_record(
            "progress_log",
            fields={
                "task_id": request.task_id,
                "command_id": command_id,
                "report_date": now.date().isoformat(),
                "submitted_at": now.isoformat(),
                "reporter_id": reporter_id,
                "on_behalf_of": request.on_behalf_of,
                "content": request.content,
                "progress": request.progress,
                "attachments": list(request.file_ids),
                "is_correction": False,
                "review_status": "not_required",
            },
            idempotency_key=command_id,
        )
        fields = _validate(ProgressFields, record.fields)
        if fields.command_id != command_id:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False)
        return ProgressWrite(record.id, fields.log_id)

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot:
        record = await self._teable.update_record(
            "task",
            record_id=task.record_id,
            fields={"progress": progress, "revision": next_revision},
            idempotency_key=idempotency_key,
        )
        fields = _validate(TaskFields, record.fields)
        return TaskSnapshot(
            record_id=record.id,
            task_id=fields.task_id,
            unit_id=fields.unit_id,
            primary_owner_id=task.primary_owner_id,
            progress=fields.progress,
            revision=fields.revision,
        )


def _validate(model: type[ModelT], fields: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
