from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from sd_agent.api.auth import _authenticate
from sd_agent.errors import AppError
from sd_agent.outbox.admin import AdminActor, OutboxAdminError, OutboxAdminService

router = APIRouter(prefix="/api/admin/outbox", tags=["outbox-admin"])


class ReplayApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=500)


class ReplayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID


def _service(request: Request) -> OutboxAdminService:
    service = getattr(request.app.state.resources, "outbox_admin", None)
    if not isinstance(service, OutboxAdminService):
        raise AppError("OUTBOX_ADMIN_UNAVAILABLE", "消息管理服务暂不可用", 503)
    return service


def _actor(request: Request, person_id: int, roles: frozenset[str]) -> AdminActor:
    return AdminActor(
        person_id=person_id,
        roles=roles,
        request_id=request.state.request_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", "")[:512] or None,
    )


async def _write_context(
    request: Request,
    sd_token: str | None,
    csrf_token: str | None,
) -> tuple[OutboxAdminService, AdminActor]:
    auth, user = await _authenticate(request, sd_token)
    try:
        auth.verify_csrf(user, csrf_token)
    except ValueError as exc:
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试", 403) from exc
    roles = frozenset(role.role for role in user.roles)
    return _service(request), _actor(request, user.person.person_id, roles)


@router.get("/dead-letters")
async def list_dead_letters(
    request: Request,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    _auth, user = await _authenticate(request, sd_token)
    actor = _actor(
        request,
        user.person.person_id,
        frozenset(role.role for role in user.roles),
    )
    try:
        page = await _service(request).list_dead_letters(
            actor=actor,
            cursor=str(cursor) if cursor else None,
            limit=limit,
        )
    except OutboxAdminError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return {
        "data": {
            "items": [
                {
                    "outbox_id": item.outbox_id,
                    "kind": item.kind,
                    "state": item.state,
                    "attempt_count": item.attempt_count,
                    "last_error_code": item.last_error_code,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in page.items
            ],
            "next_cursor": page.next_cursor,
        },
        "request_id": request.state.request_id,
    }


@router.post("/{outbox_id}/replay-approvals", status_code=201)
async def approve_replay(
    outbox_id: UUID,
    body: ReplayApprovalBody,
    request: Request,
    idempotency_key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    if idempotency_key is None:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "缺少幂等键", 400)
    service, actor = await _write_context(request, sd_token, csrf_token)
    try:
        approval = await service.approve_replay(
            outbox_id=str(outbox_id),
            reason=body.reason,
            idempotency_key=idempotency_key,
            actor=actor,
            now=datetime.now(UTC),
        )
    except OutboxAdminError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return {
        "data": {
            "approval_id": approval.approval_id,
            "outbox_id": approval.outbox_id,
            "approved_by": approval.approved_by,
            "approved_at": approval.approved_at.isoformat(),
            "consumed": approval.consumed,
        },
        "request_id": request.state.request_id,
    }


@router.post("/{outbox_id}/replay")
async def replay(
    outbox_id: UUID,
    body: ReplayBody,
    request: Request,
    idempotency_key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    if idempotency_key is None:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "缺少幂等键", 400)
    service, actor = await _write_context(request, sd_token, csrf_token)
    try:
        result = await service.replay(
            outbox_id=str(outbox_id),
            approval_id=str(body.approval_id),
            idempotency_key=idempotency_key,
            actor=actor,
            now=datetime.now(UTC),
        )
    except OutboxAdminError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return {
        "data": {
            "outbox_id": result.outbox_id,
            "approval_id": result.approval_id,
            "state": result.state,
            "idempotent": result.idempotent,
        },
        "request_id": request.state.request_id,
    }
