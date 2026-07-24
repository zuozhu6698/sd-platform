from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from sd_agent.adapters.oa import HttpOaGateway
from sd_agent.oa.service import CompletePendingCommand

COMMAND = CompletePendingCommand(
    command_id="11111111-1111-4111-8111-111111111111",
    task_id=101,
    person_id=7,
    log_id=88,
)
KEY = "submission:11111111-1111-4111-8111-111111111111:oa.complete_pending"


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
