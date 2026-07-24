from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from sd_agent.adapters.teable import TeableAdapterError
from sd_agent.oa import (
    MockOaGateway,
    OaGatewayResult,
    OaUrgeOutboxHandler,
    SendUrgeCommand,
)
from sd_agent.outbox import OutboxItem

NOW = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)


def item(**overrides: object) -> OutboxItem:
    payload: dict[str, object] = {
        "task_id": 101,
        "target_id": 7,
        "level": "reminder",
        "content": "【临期提醒】请及时更新进展。",
        "planned_at": "2026-07-24T09:00:00+08:00",
    }
    payload.update(overrides)
    return OutboxItem(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "oa.send_urge",
        "urge:v1:101:7:2026-07-24:reminder",
        payload,
        1,
        NOW,
    )


class FakeReceipts:
    def __init__(self) -> None:
        self.calls: list[tuple[SendUrgeCommand, dict[str, object]]] = []
        self.error: TeableAdapterError | None = None

    async def record_sent(self, command: SendUrgeCommand, **facts: object) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((command, facts))


async def test_urge_handler_sends_and_records_receipt() -> None:
    gateway = MockOaGateway()
    receipts = FakeReceipts()
    result = await OaUrgeOutboxHandler(gateway, receipts, clock=lambda: NOW)(item())
    assert result.success is True and result.status_code == 202
    assert gateway.accepted_count == 1
    command, facts = receipts.calls[0]
    assert command.task_id == 101 and command.target_id == 7
    assert facts["outbox_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert str(facts["receipt_id"]).startswith("mock-")
    assert facts["sent_at"] == NOW


async def test_urge_timeout_converges_and_receipt_write_is_retryable() -> None:
    key = item().dedup_key
    gateway = MockOaGateway(behaviors={key: "timeout_after_accept"})
    receipts = FakeReceipts()
    handler = OaUrgeOutboxHandler(gateway, receipts, clock=lambda: NOW)
    first = await handler(item())
    second = await handler(item())
    assert first.error_code == "OA_RESULT_UNKNOWN" and first.retryable is True
    assert second.success is True and len(receipts.calls) == 1
    assert gateway.accepted_count == 1

    receipts.error = TeableAdapterError("TEABLE_UNAVAILABLE", retryable=True)
    failed = await handler(item())
    assert failed.error_code == "TEABLE_UNAVAILABLE" and failed.retryable is True
    assert gateway.accepted_count == 1


@pytest.mark.parametrize(
    "message",
    [
        item(task_id=0),
        item(level="invalid"),
        item(content=""),
        item(content="bad\ncontent"),
        item(planned_at="2026-07-24T09:00:00"),
        OutboxItem("id", "oa.unknown", "key", {}, 1, NOW),
    ],
)
async def test_urge_handler_rejects_invalid_durable_message(message: OutboxItem) -> None:
    result = await OaUrgeOutboxHandler(MockOaGateway(), FakeReceipts())(message)
    assert result.success is False and result.retryable is False


class MissingReceiptGateway(MockOaGateway):
    def __init__(self, receipt_id: str | None = None) -> None:
        super().__init__()
        self.receipt_id = receipt_id

    async def send_urge(self, command: BaseModel, *, dedup_key: str) -> OaGatewayResult:
        return OaGatewayResult(True, 202, receipt_id=self.receipt_id, retryable=False)


@pytest.mark.parametrize("receipt_id", [None, "", "x" * 129])
async def test_urge_handler_rejects_success_without_receipt(receipt_id: str | None) -> None:
    handler = OaUrgeOutboxHandler(
        MissingReceiptGateway(receipt_id),
        FakeReceipts(),
    )
    result = await handler(item())
    assert result.error_code == "OA_RESPONSE_INVALID" and result.retryable is False


async def test_urge_handler_rejects_naive_receipt_clock() -> None:
    handler = OaUrgeOutboxHandler(
        MockOaGateway(),
        FakeReceipts(),
        clock=lambda: datetime(2026, 7, 24, 1, 0),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        await handler(item())


class FakeTeable:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], str]] = []

    async def create_record(
        self, table: str, *, fields: dict[str, object], idempotency_key: str
    ) -> object:
        self.calls.append((table, fields, idempotency_key))
        return object()


async def test_teable_receipt_store_uses_dedup_key_and_final_content() -> None:
    from sd_agent.oa import TeableUrgeReceiptStore

    teable = FakeTeable()
    store = TeableUrgeReceiptStore(teable)  # type: ignore[arg-type]
    command = SendUrgeCommand.model_validate(item().payload)
    await store.record_sent(
        command,
        outbox_id=item().outbox_id,
        dedup_key=item().dedup_key,
        receipt_id="oa-1",
        sent_at=NOW,
    )
    table, fields, key = teable.calls[0]
    assert table == "urge_log" and key == item().dedup_key
    assert fields["content"] == command.content and fields["result"] == "sent"
