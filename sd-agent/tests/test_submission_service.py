from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from sd_agent.auth import Principal
from sd_agent.auth.service import AuthenticatedUser, PersonIdentity, RoleScope, SessionState
from sd_agent.submission import (
    CommandSnapshot,
    CommandState,
    ProgressWrite,
    SubmissionError,
    SubmissionInput,
    SubmissionResult,
    SubmissionService,
    TaskSnapshot,
)
from sd_agent.submission.service import AuditFacts, new_command_id

NOW = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = UUID("11111111-1111-4111-8111-111111111111")


def user(*roles: RoleScope, person_id: int = 7) -> AuthenticatedUser:
    principal = Principal(person_id, "sid", "v1", NOW, NOW + timedelta(hours=1))
    session = SessionState("sid", person_id, "v1", "csrf", NOW + timedelta(hours=1), None)
    person = PersonIdentity(person_id, "张三", 10, True, 1)
    return AuthenticatedUser(principal, session, person, tuple(roles))


def request(**overrides: object) -> SubmissionInput:
    values: dict[str, object] = {
        "task_id": 101,
        "content": "本周完成接口联调并形成测试记录",
        "progress": 65,
        "file_ids": (),
        "on_behalf_of": None,
        "task_revision": 7,
    }
    values.update(overrides)
    return SubmissionInput(**values)  # type: ignore[arg-type]


class FakePersistence:
    def __init__(self) -> None:
        self.command: CommandSnapshot | None = None
        self.completed: tuple[SubmissionResult, AuditFacts] | None = None
        self.rejected: str | None = None
        self.locked = False

    @asynccontextmanager
    async def task_lock(self, task_id: int) -> AsyncIterator[None]:
        assert task_id == 101
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    async def reserve(
        self,
        *,
        idempotency_key: str,
        person_id: int,
        task_id: int,
        payload_hash: str,
        now: datetime,
    ) -> CommandSnapshot:
        assert self.locked
        if self.command is None:
            self.command = CommandSnapshot(
                new_command_id(),
                idempotency_key,
                person_id,
                task_id,
                payload_hash,
                CommandState.PENDING,
            )
        return self.command

    async def mark_teable_written(
        self,
        command_id: str,
        record_id: str,
        *,
        now: datetime,
    ) -> None:
        assert self.command is not None and self.command.command_id == command_id
        self.command = replace(
            self.command,
            state=CommandState.TEABLE_WRITTEN,
            teable_record_id=record_id,
        )

    async def reject(self, command_id: str, code: str, *, now: datetime) -> None:
        assert self.command is not None
        self.rejected = code
        self.command = replace(
            self.command,
            state=CommandState.REJECTED,
            last_error_code=code,
        )

    async def complete(
        self,
        command_id: str,
        result: SubmissionResult,
        audit: AuditFacts,
        *,
        now: datetime,
    ) -> None:
        assert self.command is not None
        self.completed = (result, audit)
        self.command = replace(self.command, state=CommandState.COMMITTED, result=result)


class FakeGateway:
    def __init__(self) -> None:
        self.task: TaskSnapshot | None = TaskSnapshot("rec_task", 101, 10, 7, 50, 7)
        self.files_clean = True
        self.existing_progress: ProgressWrite | None = None
        self.append_calls = 0
        self.update_calls = 0
        self.bad_update = False

    async def get_task(self, task_id: int) -> TaskSnapshot | None:
        return self.task if self.task and self.task.task_id == task_id else None

    async def files_are_clean(
        self,
        file_ids: tuple[str, ...],
        *,
        task_id: int,
        person_id: int,
    ) -> bool:
        return self.files_clean

    async def find_progress_by_command(self, command_id: str) -> ProgressWrite | None:
        return self.existing_progress

    async def append_progress(
        self,
        command_id: str,
        submission: SubmissionInput,
        *,
        reporter_id: int,
        now: datetime,
    ) -> ProgressWrite:
        self.append_calls += 1
        return ProgressWrite("rec_log", 9001)

    async def update_task_progress(
        self,
        task: TaskSnapshot,
        *,
        progress: int,
        next_revision: int,
        idempotency_key: str,
    ) -> TaskSnapshot:
        self.update_calls += 1
        if self.bad_update:
            return task
        self.task = replace(task, progress=progress, revision=next_revision)
        return self.task


async def submit(
    persistence: FakePersistence,
    gateway: FakeGateway,
    *,
    submission: SubmissionInput | None = None,
    authenticated_user: AuthenticatedUser | None = None,
    now: datetime = NOW,
) -> SubmissionResult:
    return await SubmissionService(persistence=persistence, gateway=gateway).submit(
        user=authenticated_user or user(),
        request=submission or request(),
        idempotency_key=IDEMPOTENCY_KEY,
        request_id="req_1",
        ip="192.0.2.1",
        user_agent="test-agent",
        now=now,
    )


async def test_owner_submission_commits_audit_and_outbox_intent() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    result = await submit(persistence, gateway)
    assert result.log_id == 9001
    assert gateway.append_calls == 1
    assert gateway.update_calls == 1
    assert persistence.command is not None
    assert persistence.command.state is CommandState.COMMITTED
    assert persistence.completed is not None
    audit = persistence.completed[1]
    assert audit.request_id == "req_1"
    assert audit.task_id == 101


async def test_committed_duplicate_returns_same_result_without_external_writes() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    first = await submit(persistence, gateway)
    second = await submit(persistence, gateway)
    assert second == first
    assert gateway.append_calls == 1
    assert gateway.update_calls == 1


async def test_same_key_with_changed_payload_is_conflict() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    await submit(persistence, gateway)
    with pytest.raises(SubmissionError, match="幂等键") as captured:
        await submit(persistence, gateway, submission=request(progress=66))
    assert captured.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    ("submission", "task", "identity", "files_clean", "code"),
    [
        (request(), None, user(), True, "TASK_NOT_FOUND"),
        (
            request(content="太短"),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            True,
            "REPORT_TOO_SHORT",
        ),
        (
            request(progress=101),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            True,
            "REPORT_INVALID",
        ),
        (
            request(task_revision=-1),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            True,
            "REPORT_INVALID",
        ),
        (
            request(file_ids=("unsafe",)),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            True,
            "FILE_NOT_CLEAN",
        ),
        (
            request(progress=49),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            True,
            "PROGRESS_REGRESSION_REQUIRES_REVIEW",
        ),
        (request(), TaskSnapshot("r", 101, 10, 8, 50, 7), user(), True, "TASK_FORBIDDEN"),
        (
            request(on_behalf_of=7),
            TaskSnapshot("r", 101, 11, 7, 50, 7),
            user(RoleScope("unit_coordinator", 10), person_id=8),
            True,
            "TASK_FORBIDDEN",
        ),
        (
            request(file_ids=("file_abcdefgh",)),
            TaskSnapshot("r", 101, 10, 7, 50, 7),
            user(),
            False,
            "FILE_NOT_CLEAN",
        ),
        (request(), TaskSnapshot("r", 101, 10, 7, 50, 9), user(), True, "TASK_STALE_REVISION"),
        (request(), TaskSnapshot("r", 101, 10, 7, 64, 8), user(), True, "TASK_STALE_REVISION"),
    ],
)
async def test_rejected_submission_is_persisted(
    submission: SubmissionInput,
    task: TaskSnapshot | None,
    identity: AuthenticatedUser,
    files_clean: bool,
    code: str,
) -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    gateway.task = task
    gateway.files_clean = files_clean
    with pytest.raises(SubmissionError) as captured:
        await submit(
            persistence,
            gateway,
            submission=submission,
            authenticated_user=identity,
        )
    assert captured.value.code == code
    assert persistence.rejected == code
    assert gateway.append_calls == 0


async def test_scoped_coordinator_can_submit_on_behalf_of_primary_owner() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    coordinator = user(RoleScope("unit_coordinator", 10), person_id=8)
    result = await submit(
        persistence,
        gateway,
        submission=request(on_behalf_of=7),
        authenticated_user=coordinator,
    )
    assert result.state == "committed"


async def test_supervision_admin_can_submit_on_behalf_across_scope() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    admin = user(RoleScope("supervision_admin", None), person_id=8)
    result = await submit(
        persistence,
        gateway,
        submission=request(on_behalf_of=7),
        authenticated_user=admin,
    )
    assert result.state == "committed"


async def test_retry_recovers_progress_created_before_process_crash() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    gateway.existing_progress = ProgressWrite("rec_existing", 99)
    result = await submit(persistence, gateway)
    assert result.log_id == 99
    assert gateway.append_calls == 0
    assert persistence.command is not None
    assert persistence.command.teable_record_id == "rec_existing"


async def test_retry_after_task_update_skips_second_update() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    normalized = request().normalized()
    first_hash_persistence = FakePersistence()
    await submit(first_hash_persistence, gateway)
    assert gateway.task is not None
    persisted = first_hash_persistence.command
    assert persisted is not None
    persistence.command = replace(
        persisted,
        state=CommandState.TEABLE_WRITTEN,
        result=None,
    )
    gateway.existing_progress = ProgressWrite("rec_log", 9001)
    gateway.update_calls = 0
    result = await submit(persistence, gateway, submission=normalized)
    assert result.log_id == 9001
    assert gateway.update_calls == 0


async def test_unconfirmed_task_update_is_retryable() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    gateway.bad_update = True
    with pytest.raises(SubmissionError) as captured:
        await submit(persistence, gateway)
    assert captured.value.code == "TEABLE_WRITE_UNCONFIRMED"
    assert captured.value.retryable is True
    assert persistence.completed is None


async def test_rejected_command_replays_same_stable_error() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    with pytest.raises(SubmissionError):
        await submit(persistence, gateway, submission=request(content="短"))
    with pytest.raises(SubmissionError) as captured:
        await submit(persistence, gateway, submission=request(content="短"))
    assert captured.value.code == "REPORT_TOO_SHORT"
    assert gateway.append_calls == 0


async def test_normalization_makes_whitespace_and_file_order_idempotent() -> None:
    persistence = FakePersistence()
    gateway = FakeGateway()
    with_files = request(
        content="本周  完成接口联调并形成测试记录",
        file_ids=("file_bbbbbbbb", "file_aaaaaaaa", "file_bbbbbbbb"),
    )
    await submit(persistence, gateway, submission=with_files)
    gateway.files_clean = True
    equivalent = request(
        content="本周 完成接口联调并形成测试记录",
        file_ids=("file_aaaaaaaa", "file_bbbbbbbb"),
    )
    assert await submit(persistence, gateway, submission=equivalent)


@pytest.mark.parametrize(
    "value",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))],
)
async def test_submission_requires_utc(value: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await submit(FakePersistence(), FakeGateway(), now=value)
