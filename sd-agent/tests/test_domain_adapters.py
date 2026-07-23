from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sd_agent.adapters.identity import TeableIdentityStore
from sd_agent.adapters.submission import TeableSubmissionGateway
from sd_agent.adapters.teable import TeableAdapterError, TeableRecord
from sd_agent.submission import SubmissionInput, TaskSnapshot

NOW = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)


class FakeTeable:
    def __init__(self) -> None:
        self.records: dict[str, list[TeableRecord]] = {}
        self.created: tuple[str, dict[str, Any], str] | None = None
        self.updated: tuple[str, str, dict[str, Any], str] | None = None

    async def list_records(self, table: str, **_kwargs: Any) -> list[TeableRecord]:
        return self.records.get(table, [])

    async def create_record(
        self,
        table: str,
        *,
        fields: dict[str, Any],
        idempotency_key: str,
    ) -> TeableRecord:
        self.created = (table, fields, idempotency_key)
        return TeableRecord(
            id="rec_log",
            fields={**fields, "log_id": 88},
        )

    async def update_record(
        self,
        table: str,
        *,
        record_id: str,
        fields: dict[str, Any],
        idempotency_key: str,
    ) -> TeableRecord:
        self.updated = (table, record_id, fields, idempotency_key)
        return TeableRecord(
            id=record_id,
            fields={"task_id": 101, "unit_id": 10, **fields},
        )


def identity_store(teable: FakeTeable) -> TeableIdentityStore:
    return TeableIdentityStore(teable)  # type: ignore[arg-type]


def submission_gateway(teable: FakeTeable) -> TeableSubmissionGateway:
    gateway = object.__new__(TeableSubmissionGateway)
    gateway._teable = teable  # type: ignore[attr-defined]
    return gateway


async def test_identity_store_returns_person_and_filters_active_roles() -> None:
    teable = FakeTeable()
    teable.records["person"] = [
        TeableRecord(
            id="rec_person",
            fields={
                "person_id": 7,
                "name": "张三",
                "unit_id": 10,
                "active": True,
                "authz_version": 2,
            },
        )
    ]
    teable.records["role_assignment"] = [
        TeableRecord(
            id="active",
            fields={
                "person_id": 7,
                "role": "domain_owner",
                "scope_unit_id": 10,
                "active": True,
                "valid_from": NOW - timedelta(days=1),
                "valid_until": NOW + timedelta(days=1),
            },
        ),
        TeableRecord(
            id="expired",
            fields={
                "person_id": 7,
                "role": "leader",
                "active": True,
                "valid_until": NOW,
            },
        ),
        TeableRecord(
            id="disabled",
            fields={"person_id": 7, "role": "leader", "active": False},
        ),
        TeableRecord(
            id="future",
            fields={
                "person_id": 7,
                "role": "leader",
                "active": True,
                "valid_from": NOW + timedelta(seconds=1),
            },
        ),
        TeableRecord(
            id="other",
            fields={"person_id": 8, "role": "leader", "active": True},
        ),
    ]
    store = identity_store(teable)
    person = await store.get_person(7)
    roles = await store.get_active_roles(7, now=NOW)
    assert person is not None and person.name == "张三"
    assert [(role.role, role.scope_unit_id) for role in roles] == [("domain_owner", 10)]


async def test_identity_store_handles_missing_conflict_and_invalid_payload() -> None:
    teable = FakeTeable()
    store = identity_store(teable)
    assert await store.get_person(7) is None
    teable.records["person"] = [
        TeableRecord(id="a", fields={}),
        TeableRecord(id="b", fields={}),
    ]
    with pytest.raises(TeableAdapterError, match="TEABLE_IDENTITY_CONFLICT"):
        await store.get_person(7)
    teable.records["person"] = [TeableRecord(id="a", fields={"person_id": "bad"})]
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await store.get_person(7)


async def test_submission_gateway_gets_task_and_exact_primary_owner() -> None:
    teable = FakeTeable()
    teable.records["task"] = [
        TeableRecord(
            id="rec_task",
            fields={"task_id": 101, "unit_id": 10, "progress": 50, "revision": 7},
        )
    ]
    teable.records["task_owner"] = [
        TeableRecord(
            id="owner",
            fields={
                "task_id": 101,
                "person_id": 7,
                "owner_type": "primary",
                "active": True,
            },
        ),
        TeableRecord(
            id="collaborator",
            fields={
                "task_id": 101,
                "person_id": 8,
                "owner_type": "collaborator",
                "active": True,
            },
        ),
    ]
    task = await submission_gateway(teable).get_task(101)
    assert task == TaskSnapshot("rec_task", 101, 10, 7, 50, 7)


async def test_submission_gateway_rejects_task_and_owner_conflicts() -> None:
    teable = FakeTeable()
    gateway = submission_gateway(teable)
    assert await gateway.get_task(101) is None
    teable.records["task"] = [TeableRecord(id="a", fields={}), TeableRecord(id="b", fields={})]
    with pytest.raises(TeableAdapterError, match="TEABLE_TASK_CONFLICT"):
        await gateway.get_task(101)
    teable.records["task"] = [
        TeableRecord(
            id="a",
            fields={"task_id": 101, "unit_id": 10, "progress": 1, "revision": 1},
        )
    ]
    with pytest.raises(TeableAdapterError, match="TEABLE_OWNER_CONFLICT"):
        await gateway.get_task(101)


async def test_progress_lookup_append_and_task_update() -> None:
    teable = FakeTeable()
    gateway = submission_gateway(teable)
    assert await gateway.find_progress_by_command("cmd") is None
    teable.records["progress_log"] = [
        TeableRecord(id="rec_existing", fields={"log_id": 77, "command_id": "cmd"})
    ]
    existing = await gateway.find_progress_by_command("cmd")
    assert existing is not None and existing.log_id == 77
    write = await gateway.append_progress(
        "cmd",
        SubmissionInput(101, "足够长度的进展说明文本", 65, (), None, 7),
        reporter_id=7,
        now=NOW,
    )
    assert write.log_id == 88
    assert teable.created is not None
    assert teable.created[1]["submitted_at"] == NOW.isoformat()
    updated = await gateway.update_task_progress(
        TaskSnapshot("rec_task", 101, 10, 7, 50, 7),
        progress=65,
        next_revision=8,
        idempotency_key="idem",
    )
    assert updated.progress == 65 and updated.revision == 8


async def test_progress_conflicts_and_mismatched_command_are_rejected() -> None:
    teable = FakeTeable()
    gateway = submission_gateway(teable)
    teable.records["progress_log"] = [
        TeableRecord(id="a", fields={}),
        TeableRecord(id="b", fields={}),
    ]
    with pytest.raises(TeableAdapterError, match="TEABLE_PROGRESS_CONFLICT"):
        await gateway.find_progress_by_command("cmd")
    teable.records["progress_log"] = [
        TeableRecord(id="a", fields={"log_id": 1, "command_id": "other"})
    ]
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await gateway.find_progress_by_command("cmd")
