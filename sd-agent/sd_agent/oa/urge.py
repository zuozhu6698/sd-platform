from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from sd_agent.adapters.teable import TeableAdapterError, TeableClient
from sd_agent.oa.service import OaGatewayResult
from sd_agent.outbox import DispatchResult, OutboxItem


class SendUrgeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: int = Field(gt=0)
    target_id: int = Field(gt=0)
    level: Literal["reminder", "overdue", "escalated"]
    content: str = Field(min_length=1, max_length=500)
    planned_at: datetime

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("urge content contains control characters")
        return value

    @field_validator("planned_at")
    @classmethod
    def validate_planned_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("planned_at must be timezone-aware")
        return value


class OaUrgeGateway(Protocol):
    async def send_urge(
        self,
        command: SendUrgeCommand,
        *,
        dedup_key: str,
    ) -> OaGatewayResult: ...


class UrgeReceiptStore(Protocol):
    async def record_sent(
        self,
        command: SendUrgeCommand,
        *,
        outbox_id: str,
        dedup_key: str,
        receipt_id: str,
        sent_at: datetime,
    ) -> None: ...


class OaUrgeOutboxHandler:
    def __init__(
        self,
        gateway: OaUrgeGateway,
        receipts: UrgeReceiptStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._gateway = gateway
        self._receipts = receipts
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(self, item: OutboxItem) -> DispatchResult:
        if item.kind != "oa.send_urge":
            return DispatchResult(
                False,
                error_code="OA_KIND_UNSUPPORTED",
                redacted_error="OA_KIND_UNSUPPORTED",
                retryable=False,
            )
        try:
            command = SendUrgeCommand.model_validate(item.payload)
        except ValidationError:
            return DispatchResult(
                False,
                error_code="OA_PAYLOAD_INVALID",
                redacted_error="OA_PAYLOAD_INVALID",
                retryable=False,
            )
        result = await self._gateway.send_urge(command, dedup_key=item.dedup_key)
        if result.success:
            if (
                not isinstance(result.receipt_id, str)
                or not result.receipt_id
                or len(result.receipt_id) > 128
            ):
                return DispatchResult(
                    False,
                    status_code=result.status_code,
                    error_code="OA_RESPONSE_INVALID",
                    redacted_error="OA_RESPONSE_INVALID",
                    retryable=False,
                )
            try:
                await self._receipts.record_sent(
                    command,
                    outbox_id=item.outbox_id,
                    dedup_key=item.dedup_key,
                    receipt_id=result.receipt_id,
                    sent_at=_aware(self._clock()),
                )
            except TeableAdapterError as exc:
                return DispatchResult(
                    False,
                    error_code=exc.code,
                    redacted_error=exc.code,
                    retryable=exc.retryable,
                )
        return DispatchResult(
            result.success,
            status_code=result.status_code,
            error_code=result.error_code,
            redacted_error=result.error_code,
            retryable=result.retryable,
        )


class TeableUrgeReceiptStore:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def record_sent(
        self,
        command: SendUrgeCommand,
        *,
        outbox_id: str,
        dedup_key: str,
        receipt_id: str,
        sent_at: datetime,
    ) -> None:
        await self._teable.create_record(
            "urge_log",
            fields={
                "urge_id": outbox_id,
                "task_id": command.task_id,
                "outbox_id": outbox_id,
                "type": "deadline",
                "level": command.level,
                "target_id": command.target_id,
                "content": command.content,
                "dedup_key": dedup_key,
                "planned_at": command.planned_at.isoformat(),
                "sent_at": sent_at.isoformat(),
                "oa_msg_id": receipt_id,
                "result": "sent",
            },
            idempotency_key=dedup_key,
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OA receipt time must be timezone-aware")
    return value
