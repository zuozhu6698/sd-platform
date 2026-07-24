from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Header, Query, Request
from pydantic import BaseModel, ConfigDict

from sd_agent.api.auth import _authenticate
from sd_agent.errors import AppError
from sd_agent.scheduler.admin import (
    SchedulerAdminActor,
    SchedulerAdminError,
    SchedulerAdminService,
)

router = APIRouter(prefix="/api/admin/runs", tags=["scheduler-admin"])


class TriggerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry_of_job_run_id: UUID | None = None


def _service(request: Request) -> SchedulerAdminService:
    service = getattr(request.app.state.resources, "scheduler_admin", None)
    if not isinstance(service, SchedulerAdminService):
        raise AppError("SCHEDULER_ADMIN_UNAVAILABLE", "计划任务管理服务暂不可用", 503)
    return service


def _actor(request: Request, person_id: int, roles: frozenset[str]) -> SchedulerAdminActor:
    return SchedulerAdminActor(
        person_id,
        roles,
        request.state.request_id,
        request.client.host if request.client else None,
        request.headers.get("User-Agent", "")[:512] or None,
    )


@router.get("")
async def list_runs(
    request: Request,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    _auth, user = await _authenticate(request, sd_token)
    actor = _actor(request, user.person.person_id, frozenset(role.role for role in user.roles))
    try:
        page = await _service(request).list_runs(
            actor=actor, cursor=str(cursor) if cursor else None, limit=limit
        )
    except SchedulerAdminError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return {
        "data": {
            "items": [
                {
                    "job_run_id": item.job_run_id,
                    "job": item.job,
                    "scheduled_for": item.scheduled_for.isoformat(),
                    "state": item.state,
                    "counts": item.counts,
                    "error_code": item.error_code,
                    "started_at": item.started_at.isoformat() if item.started_at else None,
                    "finished_at": item.finished_at.isoformat() if item.finished_at else None,
                }
                for item in page.items
            ],
            "next_cursor": page.next_cursor,
        },
        "request_id": request.state.request_id,
    }


@router.post("/{job}/trigger", status_code=202)
async def trigger_job(
    job: str,
    body: TriggerBody,
    request: Request,
    idempotency_key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    if idempotency_key is None:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "缺少幂等键", 400)
    auth, user = await _authenticate(request, sd_token)
    try:
        auth.verify_csrf(user, csrf_token)
    except ValueError as exc:
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试", 403) from exc
    actor = _actor(request, user.person.person_id, frozenset(role.role for role in user.roles))
    try:
        result = await _service(request).trigger(
            job=job,
            retry_of_job_run_id=(
                str(body.retry_of_job_run_id) if body.retry_of_job_run_id else None
            ),
            idempotency_key=idempotency_key,
            actor=actor,
            now=datetime.now(UTC),
        )
    except SchedulerAdminError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return {
        "data": {
            "trigger_id": result.trigger_id,
            "outbox_id": result.outbox_id,
            "job": result.job,
            "scheduled_for": result.scheduled_for.isoformat(),
            "retry_of_job_run_id": result.retry_of_job_run_id,
            "state": result.state,
            "idempotent": result.idempotent,
        },
        "request_id": request.state.request_id,
    }
