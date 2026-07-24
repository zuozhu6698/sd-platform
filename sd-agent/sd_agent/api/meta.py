from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sd_agent import __version__

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "data": {
            "name": settings.APP_NAME,
            "version": __version__,
            "environment": settings.ENV.value,
            "features": {
                "dev_login": settings.AUTH_DEV_LOGIN,
                "scheduler": settings.CRON_ENABLED,
                "outbox": settings.OUTBOX_ENABLED,
            },
        },
        "request_id": request.state.request_id,
    }
