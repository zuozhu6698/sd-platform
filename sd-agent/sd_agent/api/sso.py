from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, NoReturn
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from sd_agent.errors import AppError
from sd_agent.sso import SsoError, SsoService

router = APIRouter(prefix="/api/sso", tags=["sso"])


def _service(request: Request) -> SsoService:
    service = getattr(request.app.state.resources, "sso", None)
    if not isinstance(service, SsoService):
        raise AppError("SSO_UNAVAILABLE", "单点登录服务暂不可用", 503)
    return service


def _raise_safe(exc: SsoError) -> NoReturn:
    status = {
        "SSO_REDIRECT_INVALID": 400,
        "SSO_PERSON_INACTIVE": 403,
        "SSO_STUB_DISABLED": 404,
    }.get(exc.code, 401)
    messages = {
        "SSO_REDIRECT_INVALID": "登录返回路径无效",
        "SSO_PERSON_INACTIVE": "账号不存在或已停用",
        "SSO_STUB_DISABLED": "测试登录入口未启用",
    }
    raise AppError(exc.code, messages.get(exc.code, "登录票据无效或已使用"), status) from exc


@router.get("/oa/start")
async def start(
    request: Request,
    redirect: Annotated[str, Query()] = "/home",
) -> RedirectResponse:
    try:
        result = await _service(request).start(redirect, now=datetime.now(UTC))
    except SsoError as exc:
        _raise_safe(exc)
    response = RedirectResponse(result.authorization_url, status_code=302)
    response.set_cookie(
        "sd_sso_state",
        result.state,
        max_age=300,
        path="/api/sso/oa/callback",
        secure=request.app.state.resources.settings.COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/stub/authorize")
async def authorize_stub(
    request: Request,
    state: Annotated[str, Query(min_length=1, max_length=512)],
    nonce: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    try:
        ticket = _service(request).authorize_stub(state, nonce)
    except SsoError as exc:
        _raise_safe(exc)
    query = urlencode({"ticket": ticket, "state": state})
    return RedirectResponse(f"/api/sso/oa/callback?{query}", status_code=302)


@router.get("/oa/callback")
async def callback(
    request: Request,
    ticket: Annotated[str, Query(min_length=1, max_length=512)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    now = datetime.now(UTC)
    cookie_state = request.cookies.get("sd_sso_state", "")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        _raise_safe(SsoError("SSO_STATE_INVALID"))
    try:
        result = await _service(request).callback(
            ticket=ticket,
            state=state,
            request_id=request.state.request_id,
            now=now,
        )
    except SsoError as exc:
        _raise_safe(exc)
    response = RedirectResponse(result.redirect_path, status_code=302)
    response.delete_cookie("sd_sso_state", path="/api/sso/oa/callback")
    max_age = max(0, int((result.expires_at - now).total_seconds()))
    response.set_cookie(
        "sd_token",
        result.token,
        max_age=max_age,
        path="/",
        secure=request.app.state.resources.settings.COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return response
