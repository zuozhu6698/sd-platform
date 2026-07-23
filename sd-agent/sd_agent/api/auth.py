from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Header, Request
from fastapi.responses import JSONResponse

from sd_agent.auth.service import (
    AuthenticatedUser,
    AuthenticationError,
    AuthService,
    capabilities,
)
from sd_agent.errors import AppError

router = APIRouter(prefix="/api", tags=["auth"])


def _service(request: Request) -> AuthService:
    service = getattr(request.app.state.resources, "auth", None)
    if not isinstance(service, AuthService):
        raise AppError("AUTH_UNAVAILABLE", "身份服务暂不可用", 503)
    return service


async def _authenticate(
    request: Request,
    token: str | None,
) -> tuple[AuthService, AuthenticatedUser]:
    if not token:
        raise AppError("AUTH_REQUIRED", "请先登录", 401)
    service = _service(request)
    try:
        user = await service.authenticate(token, now=datetime.now(UTC))
    except AuthenticationError as exc:
        raise AppError("SESSION_INVALID", "登录状态已失效，请重新登录", 401) from exc
    return service, user


@router.get("/me")
async def me(
    request: Request,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    service, user = await _authenticate(request, sd_token)
    try:
        csrf_token = await service.issue_csrf(user, now=datetime.now(UTC))
    except AuthenticationError as exc:
        raise AppError("SESSION_INVALID", "登录状态已失效，请重新登录", 401) from exc
    return {
        "data": {
            "person": {
                "person_id": user.person.person_id,
                "name": user.person.name,
                "unit_id": user.person.unit_id,
            },
            "roles": [
                {"role": role.role, "scope_unit_id": role.scope_unit_id} for role in user.roles
            ],
            "can": capabilities(user.roles),
            "csrf_token": csrf_token,
        },
        "request_id": request.state.request_id,
    }


@router.post("/logout")
async def logout(
    request: Request,
    sd_token: Annotated[str | None, Cookie()] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> JSONResponse:
    service, user = await _authenticate(request, sd_token)
    try:
        service.verify_csrf(user, csrf_token)
        await service.logout(user, now=datetime.now(UTC))
    except AuthenticationError as exc:
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试", 403) from exc
    response = JSONResponse(
        status_code=200,
        content={"data": {"logged_out": True}, "request_id": request.state.request_id},
    )
    response.delete_cookie("sd_token", path="/", secure=True, httponly=True, samesite="lax")
    return response
