from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from sd_agent.adapters.teable import (
    TABLE_FIELDS,
    TeableAdapterError,
    TeableClient,
    TeableFilter,
)

TABLE_IDS = {name: f"tbl_{name}" for name in TABLE_FIELDS}
TEST_BEARER = "test-bearer-value"


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def client(http: httpx.AsyncClient) -> TeableClient:
    return TeableClient(
        base_url="https://teable.example.test/",
        token=TEST_BEARER,
        table_ids=TABLE_IDS,
        http=http,
    )


def test_client_rejects_incomplete_mapping_and_empty_token(http_client: httpx.AsyncClient) -> None:
    with pytest.raises(ValueError, match="nine"):
        TeableClient(base_url="https://t", token=TEST_BEARER, table_ids={}, http=http_client)
    with pytest.raises(ValueError, match="token"):
        TeableClient(base_url="https://t", token="", table_ids=TABLE_IDS, http=http_client)


async def test_list_records_uses_bearer_projection_filter_and_pagination() -> None:
    observed: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request
        return httpx.Response(
            200,
            json={"records": [{"id": "rec_1", "fields": {"person_id": 7}, "ignored": 1}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        records = await client(http).list_records(
            "person",
            projection=("person_id", "name"),
            filter_by=TeableFilter("person_id", "is", 7),
            take=20,
            skip=5,
        )

    assert records[0].id == "rec_1"
    assert observed is not None
    assert observed.url.path == "/api/table/tbl_person/record"
    assert observed.headers["Authorization"] == f"Bearer {TEST_BEARER}"
    assert observed.url.params.get_list("projection") == ["person_id", "name"]
    assert observed.url.params["take"] == "20"
    parsed_filter = json.loads(observed.url.params["filter"])
    assert parsed_filter["filterSet"][0] == {
        "fieldId": "person_id",
        "operator": "is",
        "value": 7,
    }


async def test_create_record_uses_whitelist_and_idempotency_header() -> None:
    observed: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request
        return httpx.Response(201, json={"records": [{"id": "rec_new", "fields": {}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        record = await client(http).create_record(
            "progress_log",
            fields={"task_id": 9, "command_id": "cmd_1", "content": "进展"},
            idempotency_key="idem_1",
        )

    assert record.id == "rec_new"
    assert observed is not None
    assert observed.headers["Idempotency-Key"] == "idem_1"
    assert json.loads(observed.content)["records"][0]["fields"]["task_id"] == 9


@pytest.mark.parametrize(
    ("table", "projection", "take", "skip"),
    [
        ("missing", ("id",), 10, 0),
        ("person", (), 10, 0),
        ("person", ("password",), 10, 0),
        ("person", ("person_id",), 0, 0),
        ("person", ("person_id",), 1001, 0),
        ("person", ("person_id",), 10, -1),
    ],
)
async def test_list_rejects_untrusted_shape(
    http_client: httpx.AsyncClient,
    table: str,
    projection: tuple[str, ...],
    take: int,
    skip: int,
) -> None:
    with pytest.raises(ValueError):
        await client(http_client).list_records(table, projection=projection, take=take, skip=skip)


async def test_filter_rejects_unknown_field_and_operator(http_client: httpx.AsyncClient) -> None:
    with pytest.raises(ValueError, match="filter field"):
        await client(http_client).list_records(
            "person",
            projection=("person_id",),
            filter_by=TeableFilter("password", "is", "x"),
        )
    with pytest.raises(ValueError, match="operator"):
        await client(http_client).list_records(
            "person",
            projection=("person_id",),
            filter_by=TeableFilter("person_id", "raw-sql", "x"),
        )


async def test_create_rejects_unknown_table_fields_and_empty_key(
    http_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(ValueError):
        await client(http_client).create_record("missing", fields={"x": 1}, idempotency_key="x")
    with pytest.raises(ValueError, match="field"):
        await client(http_client).create_record(
            "person", fields={"password": "x"}, idempotency_key="x"
        )
    with pytest.raises(ValueError, match="idempotency"):
        await client(http_client).create_record(
            "person", fields={"person_id": 1}, idempotency_key=""
        )


async def test_get_retries_retryable_failures(monkeypatch: Any) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"records": []})

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("sd_agent.adapters.teable.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        records = await client(http).list_records("person", projection=("person_id",))
    assert records == []
    assert attempts == 3


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(400, "TEABLE_REQUEST_REJECTED", False), (429, "TEABLE_UNAVAILABLE", True)],
)
async def test_http_failures_have_safe_error_contract(
    monkeypatch: Any,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("sd_agent.adapters.teable.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status))
    ) as http:
        with pytest.raises(TeableAdapterError) as captured:
            await client(http).list_records("person", projection=("person_id",))
    assert captured.value.code == code
    assert captured.value.retryable is retryable


@pytest.mark.parametrize("payload", [{}, {"records": []}, {"records": [{"id": "", "fields": {}}]}])
async def test_create_rejects_invalid_external_response(payload: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(201, json=payload))
    ) as http:
        with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
            await client(http).create_record(
                "person", fields={"person_id": 1}, idempotency_key="key"
            )


async def test_network_error_becomes_retryable_safe_failure(monkeypatch: Any) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("private endpoint details", request=request)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("sd_agent.adapters.teable.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(TeableAdapterError) as captured:
            await client(http).list_records("person", projection=("person_id",))
    assert captured.value.code == "TEABLE_UNAVAILABLE"
    assert captured.value.retryable is True
    assert attempts == 3
