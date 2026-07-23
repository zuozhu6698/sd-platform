from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request, Response

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


def choose_request_id(candidate: str | None) -> str:
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"req_{uuid4().hex}"


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = choose_request_id(request.headers.get("X-Request-Id"))
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        request_id_context.reset(token)
