from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    return {
        "data": {"status": "alive"},
        "request_id": request.state.request_id,
    }


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    ready, dependencies = await request.app.state.resources.readiness()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "data": {
                "status": "ready" if ready else "not_ready",
                "dependencies": dependencies,
            },
            "request_id": request.state.request_id,
        },
    )
