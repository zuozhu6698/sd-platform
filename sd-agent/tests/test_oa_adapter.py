from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from sd_agent.adapters.oa import HttpOaGateway
from sd_agent.oa import SendUrgeCommand
from sd_agent.oa.service import CompletePendingCommand

COMMAND = CompletePendingCommand(
    command_id="11111111-1111-4111-8111-111111111111",
    task_id=101,
    person_id=7,
    log_id=88,
)
KEY = "submission:11111111-1111-4111-8111-111111111111:oa.complete_pending"
URGE = SendUrgeCommand(
    task_id=101,
    target_id=7,
    level="reminder",
    content="【临期提醒】请及时更新进展。",
    planned_at="2026-07-24T09:00:00+08:00",
)
URGE_KEY = "urge:v1:101:7:2026-07-24:reminder"


@respx.mock
async def test_http_adapter_sends_header_credentials_and_idempotency() -> None:
    route = respx.post("https://oa.test/api/pending/complete").mock(
        return_value=httpx.Response(202, json={"receipt_id": "oa-receipt-1"})
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer top-secret-token"
    assert request.headers["X-Idempotency-Key"] == KEY
    assert "top-secret-token" not in str(result)
    assert result.success is True
    assert result.receipt_id == "oa-receipt-1"


@respx.mock
async def test_http_adapter_treats_timeout_as_unknown_without_leaking_url_or_token() -> None:
    respx.post("https://oa.test/api/pending/complete").mock(side_effect=httpx.ReadTimeout("secret"))
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)
    assert result.error_code == "OA_RESULT_UNKNOWN"
    assert result.retryable is True
    assert "secret" not in str(result)
    assert "oa.test" not in str(result)


@respx.mock
async def test_http_adapter_classifies_non_timeout_network_error() -> None:
    respx.post("https://oa.test/api/pending/complete").mock(
        side_effect=httpx.ConnectError("credential-bearing network error")
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api/",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)
    assert result.error_code == "OA_NETWORK_ERROR"
    assert "credential" not in str(result)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "OA_RATE_LIMITED", True),
        (503, "OA_UNAVAILABLE", True),
        (400, "OA_REQUEST_REJECTED", False),
    ],
)
@respx.mock
async def test_http_adapter_classifies_status_without_copying_response_body(
    status: int,
    code: str,
    retryable: bool,
) -> None:
    respx.post("https://oa.test/api/pending/complete").mock(
        return_value=httpx.Response(status, text="credential-bearing vendor response")
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)
    assert result.error_code == code
    assert result.retryable is retryable
    assert "vendor response" not in str(result)


@pytest.mark.parametrize(
    "body",
    [{}, [], {"receipt_id": ""}, {"receipt_id": 7}, {"receipt_id": "x" * 129}],
)
@respx.mock
async def test_http_adapter_rejects_malformed_success_receipt(body: object) -> None:
    respx.post("https://oa.test/api/pending/complete").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)
    assert result.error_code == "OA_RESPONSE_INVALID"
    assert result.retryable is False


@respx.mock
async def test_http_adapter_rejects_non_json_success() -> None:
    respx.post("https://oa.test/api/pending/complete").mock(
        return_value=httpx.Response(200, text="not-json")
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).complete_pending(COMMAND, dedup_key=KEY)
    assert result.error_code == "OA_RESPONSE_INVALID"


async def test_http_adapter_rejects_invalid_configuration() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="OA base URL"):
            HttpOaGateway(base_url="http://oa.test/api", token=SecretStr("x"), http=http)
        with pytest.raises(ValueError, match="OA token"):
            HttpOaGateway(base_url="https://oa.test/api", token=SecretStr(""), http=http)


@respx.mock
async def test_http_adapter_send_urge_uses_distinct_skeleton_endpoint() -> None:
    route = respx.post("https://oa.test/api/messages/urge").mock(
        return_value=httpx.Response(202, json={"receipt_id": "oa-message-1"})
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).send_urge(URGE, dedup_key=URGE_KEY)
    assert result.success is True and result.receipt_id == "oa-message-1"
    assert route.calls[0].request.headers["X-Idempotency-Key"] == URGE_KEY


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "OA_RATE_LIMITED", True),
        (503, "OA_UNAVAILABLE", True),
        (400, "OA_REQUEST_REJECTED", False),
    ],
)
@respx.mock
async def test_http_adapter_send_urge_classifies_status(
    status: int, code: str, retryable: bool
) -> None:
    respx.post("https://oa.test/api/messages/urge").mock(
        return_value=httpx.Response(status, text="unsafe vendor body")
    )
    async with httpx.AsyncClient() as http:
        result = await HttpOaGateway(
            base_url="https://oa.test/api",
            token=SecretStr("top-secret-token"),
            http=http,
        ).send_urge(URGE, dedup_key=URGE_KEY)
    assert (result.error_code, result.retryable) == (code, retryable)
    assert "unsafe vendor body" not in str(result)
