from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import UUID4, BaseModel, ConfigDict, Field, ValidationError

from sd_agent.outbox import DispatchResult, OutboxItem


class CompletePendingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID4
    task_id: int = Field(gt=0)
    person_id: int = Field(gt=0)
    log_id: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class OaGatewayResult:
    success: bool
    status_code: int | None
    receipt_id: str | None = None
    error_code: str | None = None
    retryable: bool = True


class OaGateway(Protocol):
    async def complete_pending(
        self,
        command: CompletePendingCommand,
        *,
        dedup_key: str,
    ) -> OaGatewayResult: ...


class OaOutboxHandler:
    def __init__(self, gateway: OaGateway) -> None:
        self._gateway = gateway

    async def __call__(self, item: OutboxItem) -> DispatchResult:
        if item.kind != "oa.complete_pending":
            return DispatchResult(
                False,
                error_code="OA_KIND_UNSUPPORTED",
                redacted_error="OA_KIND_UNSUPPORTED",
                retryable=False,
            )
        try:
            command = CompletePendingCommand.model_validate(item.payload)
        except ValidationError:
            return DispatchResult(
                False,
                error_code="OA_PAYLOAD_INVALID",
                redacted_error="OA_PAYLOAD_INVALID",
                retryable=False,
            )
        result = await self._gateway.complete_pending(command, dedup_key=item.dedup_key)
        return DispatchResult(
            result.success,
            status_code=result.status_code,
            error_code=result.error_code,
            redacted_error=result.error_code,
            retryable=result.retryable,
        )


MockBehavior = Literal["timeout_after_accept", "rate_limit", "unavailable", "reject"]


class MockOaGateway:
    """有状态 OA 替身：复现幂等、未知结果和失败分类，不模拟真实联调成功。"""

    def __init__(self, *, behaviors: dict[str, MockBehavior] | None = None) -> None:
        self._behaviors = dict(behaviors or {})
        self._accepted: dict[str, tuple[str, str]] = {}

    @property
    def accepted_count(self) -> int:
        return len(self._accepted)

    async def complete_pending(
        self,
        command: CompletePendingCommand,
        *,
        dedup_key: str,
    ) -> OaGatewayResult:
        return self._accept(command, dedup_key=dedup_key)

    async def send_urge(
        self,
        command: BaseModel,
        *,
        dedup_key: str,
    ) -> OaGatewayResult:
        return self._accept(command, dedup_key=dedup_key)

    def _accept(self, command: BaseModel, *, dedup_key: str) -> OaGatewayResult:
        payload_hash = _command_hash(command)
        existing = self._accepted.get(dedup_key)
        if existing is not None:
            existing_hash, receipt_id = existing
            if existing_hash != payload_hash:
                return OaGatewayResult(
                    False,
                    409,
                    error_code="OA_IDEMPOTENCY_CONFLICT",
                    retryable=False,
                )
            return OaGatewayResult(True, 202, receipt_id=receipt_id, retryable=False)

        behavior = self._behaviors.get(dedup_key)
        if behavior == "rate_limit":
            return OaGatewayResult(False, 429, error_code="OA_RATE_LIMITED")
        if behavior == "unavailable":
            return OaGatewayResult(False, 503, error_code="OA_UNAVAILABLE")
        if behavior == "reject":
            return OaGatewayResult(
                False,
                400,
                error_code="OA_REQUEST_REJECTED",
                retryable=False,
            )

        receipt_id = f"mock-{hashlib.sha256(dedup_key.encode()).hexdigest()[:24]}"
        self._accepted[dedup_key] = (payload_hash, receipt_id)
        if behavior == "timeout_after_accept":
            return OaGatewayResult(False, None, error_code="OA_RESULT_UNKNOWN")
        return OaGatewayResult(True, 202, receipt_id=receipt_id, retryable=False)


def _command_hash(command: BaseModel) -> str:
    payload: dict[str, Any] = command.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
