from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.persistence.models import AuditEvent, FileObject, OutboxMessage, SubmissionCommand
from sd_agent.submission.service import (
    AuditFacts,
    CommandSnapshot,
    CommandState,
    SubmissionResult,
    new_command_id,
)


class SqlSubmissionPersistence:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def task_lock(self, task_id: int) -> AsyncIterator[None]:
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :task_id)"),
                {"namespace": 0x534450, "task_id": task_id},
            )
            yield

    async def reserve(
        self,
        *,
        idempotency_key: str,
        person_id: int,
        task_id: int,
        payload_hash: str,
        now: datetime,
    ) -> CommandSnapshot:
        command_id = new_command_id()
        statement = (
            insert(SubmissionCommand)
            .values(
                command_id=command_id,
                idempotency_key=idempotency_key,
                person_id=person_id,
                task_id=task_id,
                payload_hash=payload_hash,
                state=CommandState.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[SubmissionCommand.idempotency_key])
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
            row = await session.scalar(
                select(SubmissionCommand).where(
                    SubmissionCommand.idempotency_key == idempotency_key
                )
            )
        if row is None:
            raise RuntimeError("submission command reservation failed")
        return _command_snapshot(row)

    async def mark_teable_written(
        self,
        command_id: str,
        record_id: str,
        *,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(SubmissionCommand)
                .where(
                    SubmissionCommand.command_id == command_id,
                    SubmissionCommand.state == CommandState.PENDING.value,
                )
                .values(
                    state=CommandState.TEABLE_WRITTEN.value,
                    teable_record_id=record_id,
                    updated_at=now,
                )
            )

    async def reject(self, command_id: str, code: str, *, now: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(SubmissionCommand)
                .where(
                    SubmissionCommand.command_id == command_id,
                    SubmissionCommand.state != CommandState.COMMITTED.value,
                )
                .values(
                    state=CommandState.REJECTED.value,
                    last_error_code=code,
                    updated_at=now,
                )
            )

    async def complete(
        self,
        command_id: str,
        result: SubmissionResult,
        audit: AuditFacts,
        *,
        now: datetime,
    ) -> None:
        result_json = {
            "submission_id": result.submission_id,
            "log_id": result.log_id,
            "state": result.state,
        }
        async with self._sessions.begin() as session:
            if audit.file_ids:
                bound_files = await session.scalars(
                    update(FileObject)
                    .where(
                        FileObject.file_id.in_(audit.file_ids),
                        FileObject.owner_person_id == audit.person_id,
                        FileObject.state == "clean",
                        (FileObject.task_id.is_(None) | (FileObject.task_id == audit.task_id)),
                    )
                    .values(task_id=audit.task_id, bound_at=now)
                    .returning(FileObject.file_id)
                )
                if set(bound_files) != set(audit.file_ids):
                    raise RuntimeError("clean file binding changed during submission")
            await session.execute(
                update(SubmissionCommand)
                .where(
                    SubmissionCommand.command_id == command_id,
                    SubmissionCommand.state != CommandState.COMMITTED.value,
                )
                .values(
                    state=CommandState.COMMITTED.value,
                    result=result_json,
                    last_error_code=None,
                    updated_at=now,
                )
            )
            session.add(
                AuditEvent(
                    event_id=str(uuid4()),
                    request_id=audit.request_id,
                    who=str(audit.person_id),
                    role=",".join(audit.roles) or None,
                    scope={},
                    what="report.submit",
                    target_type="task",
                    target_id=str(audit.task_id),
                    result="success",
                    ip=audit.ip,
                    user_agent=audit.user_agent,
                    details={"command_id": command_id, "log_id": result.log_id},
                    created_at=now,
                )
            )
            session.add(
                OutboxMessage(
                    outbox_id=str(uuid4()),
                    kind="oa.complete_pending",
                    dedup_key=f"submission:{command_id}:oa.complete_pending",
                    payload={
                        "command_id": command_id,
                        "task_id": audit.task_id,
                        "person_id": audit.person_id,
                        "log_id": result.log_id,
                    },
                    state="pending",
                    available_at=now,
                    lease_until=None,
                    attempt_count=0,
                    last_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )


def _command_snapshot(row: SubmissionCommand) -> CommandSnapshot:
    return CommandSnapshot(
        command_id=row.command_id,
        idempotency_key=row.idempotency_key,
        person_id=row.person_id,
        task_id=row.task_id,
        payload_hash=row.payload_hash,
        state=CommandState(row.state),
        teable_record_id=row.teable_record_id,
        result=_submission_result(row.result),
        last_error_code=row.last_error_code,
    )


def _submission_result(value: dict[str, Any] | None) -> SubmissionResult | None:
    if value is None:
        return None
    try:
        return SubmissionResult(
            submission_id=str(value["submission_id"]),
            log_id=int(value["log_id"]),
            state=str(value["state"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("invalid persisted submission result") from exc
