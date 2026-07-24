from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sd_agent.oa.service import MockOaGateway, OaOutboxHandler
from sd_agent.outbox import OutboxItem


def item(
    *,
    dedup_key: str = "submission:11111111-1111-4111-8111-111111111111:oa.complete_pending",
    payload: dict[str, object] | None = None,
) -> OutboxItem:
    return OutboxItem(
        outbox_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        kind="oa.complete_pending",
        dedup_key=dedup_key,
        payload=payload
        if payload is not None
        else {
            "command_id": "11111111-1111-4111-8111-111111111111",
            "task_id": 101,
            "person_id": 7,
            "log_id": 88,
        },
        attempt=1,
        started_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


async def test_mock_accepts_and_deduplicates_the_same_business_command() -> None:
    gateway = MockOaGateway()
    handler = OaOutboxHandler(gateway)

    first = await handler(item())
    second = await handler(item())

    assert first.success is True
    assert first.status_code == 202
    assert second == first
    assert gateway.accepted_count == 1


async def test_mock_rejects_same_key_with_different_payload() -> None:
    gateway = MockOaGateway()
    handler = OaOutboxHandler(gateway)
    assert (await handler(item())).success is True

    result = await handler(
        item(
            payload={
                "command_id": "11111111-1111-4111-8111-111111111111",
                "task_id": 102,
                "person_id": 7,
                "log_id": 88,
            }
        )
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "OA_IDEMPOTENCY_CONFLICT"


async def test_timeout_after_acceptance_converges_on_retry_without_duplicate() -> None:
    key = item().dedup_key
    gateway = MockOaGateway(behaviors={key: "timeout_after_accept"})
    handler = OaOutboxHandler(gateway)

    first = await handler(item())
    second = await handler(item())

    assert first.success is False
    assert first.retryable is True
    assert first.error_code == "OA_RESULT_UNKNOWN"
    assert second.success is True
    assert gateway.accepted_count == 1


@pytest.mark.parametrize(
    ("behavior", "code", "retryable", "status"),
    [
        ("rate_limit", "OA_RATE_LIMITED", True, 429),
        ("unavailable", "OA_UNAVAILABLE", True, 503),
        ("reject", "OA_REQUEST_REJECTED", False, 400),
    ],
)
async def test_mock_exercises_retry_and_rejection_classes(
    behavior: str,
    code: str,
    retryable: bool,
    status: int,
) -> None:
    key = item().dedup_key
    result = await OaOutboxHandler(MockOaGateway(behaviors={key: behavior}))(item())
    assert (result.error_code, result.retryable, result.status_code) == (code, retryable, status)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"command_id": "not-a-uuid", "task_id": 1, "person_id": 2, "log_id": 3},
        {
            "command_id": "11111111-1111-4111-8111-111111111111",
            "task_id": 0,
            "person_id": 2,
            "log_id": 3,
        },
    ],
)
async def test_handler_rejects_malformed_durable_payload(payload: dict[str, object]) -> None:
    result = await OaOutboxHandler(MockOaGateway())(item(payload=payload))
    assert result.success is False
    assert result.retryable is False
    assert result.error_code == "OA_PAYLOAD_INVALID"


async def test_handler_rejects_the_wrong_outbox_kind() -> None:
    message = item()
    wrong = OutboxItem(
        message.outbox_id,
        "oa.unknown",
        message.dedup_key,
        message.payload,
        message.attempt,
        message.started_at,
    )
    result = await OaOutboxHandler(MockOaGateway())(wrong)
    assert result.error_code == "OA_KIND_UNSUPPORTED"
    assert result.retryable is False
