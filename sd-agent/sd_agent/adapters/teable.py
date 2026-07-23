from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TABLE_FIELDS: dict[str, frozenset[str]] = {
    "org_unit": frozenset(
        {"unit_id", "name", "type", "parent_id", "oa_org_id", "sort", "active", "updated_at"}
    ),
    "person": frozenset(
        {"person_id", "name", "unit_id", "oa_account", "active", "authz_version", "updated_at"}
    ),
    "role_assignment": frozenset(
        {
            "assignment_id",
            "person_id",
            "role",
            "scope_unit_id",
            "valid_from",
            "valid_until",
            "active",
        }
    ),
    "key_work": frozenset(
        {
            "kw_id",
            "year",
            "seq",
            "name",
            "goal",
            "lead_unit_id",
            "lead_person_id",
            "status",
            "progress",
            "revision",
            "updated_at",
        }
    ),
    "task": frozenset(
        {
            "task_id",
            "kw_id",
            "unit_id",
            "category",
            "content",
            "measures",
            "deadline",
            "cycle",
            "weight",
            "progress",
            "status",
            "related_tasks",
            "ai_flag",
            "ai_note",
            "exempt_until",
            "revision",
            "updated_at",
        }
    ),
    "task_owner": frozenset({"task_owner_id", "task_id", "person_id", "owner_type", "active"}),
    "progress_log": frozenset(
        {
            "log_id",
            "task_id",
            "command_id",
            "report_date",
            "submitted_at",
            "reporter_id",
            "on_behalf_of",
            "content",
            "progress",
            "attachments",
            "is_correction",
            "correction_reason",
            "approved_by",
            "ai_result",
            "ai_comment",
            "ai_question",
            "ai_run_id",
            "reply",
            "appeal",
            "review_status",
        }
    ),
    "urge_log": frozenset(
        {
            "urge_id",
            "task_id",
            "outbox_id",
            "type",
            "level",
            "target_id",
            "content",
            "dedup_key",
            "planned_at",
            "sent_at",
            "oa_msg_id",
            "result",
        }
    ),
    "work_calendar": frozenset({"calendar_date", "is_workday", "name", "source", "revision"}),
}


class TeableAdapterError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class TeableRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    fields: dict[str, Any]


class TeableRecordList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    records: list[TeableRecord]


@dataclass(frozen=True, slots=True)
class TeableFilter:
    field: str
    operator: str
    value: str | int | bool

    def to_json(self) -> str:
        if self.operator not in {"is", "isNot", "isGreater", "isLess", "contains"}:
            raise ValueError("unsupported Teable filter operator")
        return json.dumps(
            {
                "conjunction": "and",
                "filterSet": [
                    {
                        "fieldId": self.field,
                        "operator": self.operator,
                        "value": self.value,
                    }
                ],
            },
            separators=(",", ":"),
        )


class TeableClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        table_ids: dict[str, str],
        http: httpx.AsyncClient,
    ) -> None:
        if set(table_ids) != set(TABLE_FIELDS):
            raise ValueError("Teable table mapping must contain exactly nine logical tables")
        if not token:
            raise ValueError("Teable token is required")
        self._base_url = base_url.rstrip("/")
        self._table_ids = table_ids.copy()
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    async def list_records(
        self,
        table: str,
        *,
        projection: tuple[str, ...],
        filter_by: TeableFilter | None = None,
        take: int = 100,
        skip: int = 0,
    ) -> list[TeableRecord]:
        self._validate_fields(table, projection)
        if not 1 <= take <= 1000 or skip < 0:
            raise ValueError("invalid pagination")
        params: list[tuple[str, str | int]] = [
            ("fieldKeyType", "name"),
            ("cellFormat", "json"),
            ("take", take),
            ("skip", skip),
        ]
        params.extend(("projection", field) for field in projection)
        if filter_by is not None:
            if filter_by.field not in TABLE_FIELDS[table]:
                raise ValueError("Teable filter field is not allowed")
            params.append(("filter", filter_by.to_json()))
        response = await self._request_with_get_retry(
            "GET",
            self._record_url(table),
            params=params,
        )
        try:
            payload = TeableRecordList.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
        return payload.records

    async def create_record(
        self,
        table: str,
        *,
        fields: dict[str, Any],
        idempotency_key: str,
    ) -> TeableRecord:
        self._validate_fields(table, tuple(fields))
        if not idempotency_key:
            raise ValueError("idempotency key is required")
        response = await self._request_once(
            "POST",
            self._record_url(table),
            headers={**self._headers, "Idempotency-Key": idempotency_key},
            json={"fieldKeyType": "name", "typecast": False, "records": [{"fields": fields}]},
        )
        try:
            payload = TeableRecordList.model_validate(response.json())
            if len(payload.records) != 1:
                raise ValueError("expected one record")
        except (ValueError, ValidationError) as exc:
            raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
        return payload.records[0]

    def _record_url(self, table: str) -> str:
        try:
            table_id = self._table_ids[table]
        except KeyError as exc:
            raise ValueError("unknown Teable logical table") from exc
        return f"{self._base_url}/api/table/{table_id}/record"

    def _validate_fields(self, table: str, fields: tuple[str, ...]) -> None:
        try:
            allowed = TABLE_FIELDS[table]
        except KeyError as exc:
            raise ValueError("unknown Teable logical table") from exc
        if not fields or not set(fields) <= allowed:
            raise ValueError("Teable field is not allowed")

    async def _request_with_get_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                return await self._request_once(method, url, headers=self._headers, **kwargs)
            except TeableAdapterError as exc:
                if not exc.retryable or attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (2**attempt))
        raise AssertionError("unreachable")

    async def _request_once(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise TeableAdapterError("TEABLE_UNAVAILABLE", retryable=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TeableAdapterError("TEABLE_UNAVAILABLE", retryable=True)
        if response.status_code >= 400:
            raise TeableAdapterError("TEABLE_REQUEST_REJECTED", retryable=False)
        return response
