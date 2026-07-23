from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from sd_agent.adapters.teable import TeableAdapterError
from sd_agent.api.auth import _authenticate
from sd_agent.errors import AppError
from sd_agent.submission import SubmissionError, SubmissionInput, SubmissionService

router = APIRouter(prefix="/api/report", tags=["report"])


class ReportSubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=5000)
    progress: int = Field(ge=0, le=100)
    file_ids: tuple[str, ...] = Field(default=(), max_length=20)
    on_behalf_of: int | None = Field(default=None, gt=0)
    task_revision: int = Field(ge=0)


@router.post("/submit", status_code=201)
async def submit_report(
    body: ReportSubmitBody,
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
    service = getattr(request.app.state.resources, "submission", None)
    if not isinstance(service, SubmissionService):
        raise AppError("SUBMISSION_UNAVAILABLE", "填报服务暂不可用", 503)
    client_ip = request.client.host if request.client else None
    try:
        result = await service.submit(
            user=user,
            request=SubmissionInput(
                task_id=body.task_id,
                content=body.content,
                progress=body.progress,
                file_ids=body.file_ids,
                on_behalf_of=body.on_behalf_of,
                task_revision=body.task_revision,
            ),
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            ip=client_ip,
            user_agent=request.headers.get("User-Agent", "")[:512] or None,
            now=datetime.now(UTC),
        )
    except SubmissionError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    except TeableAdapterError as exc:
        status_code = 503 if exc.retryable else 502
        raise AppError(exc.code, "外部数据服务暂不可用", status_code) from exc
    return {
        "data": {
            "submission_id": result.submission_id,
            "log_id": result.log_id,
            "state": result.state,
        },
        "request_id": request.state.request_id,
    }
