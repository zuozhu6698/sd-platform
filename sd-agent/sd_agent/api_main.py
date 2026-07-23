from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sd_agent.api.auth import router as auth_router
from sd_agent.api.files import router as files_router
from sd_agent.api.health import router as health_router
from sd_agent.api.home import router as home_router
from sd_agent.api.meta import router as meta_router
from sd_agent.api.report import router as report_router
from sd_agent.config import Settings
from sd_agent.errors import install_error_handlers
from sd_agent.logging_config import configure_logging
from sd_agent.request_context import request_id_middleware
from sd_agent.runtime import RuntimeResources


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()
    configure_logging(active_settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.resources = RuntimeResources.create(active_settings)
        yield
        await app.state.resources.close()

    app = FastAPI(
        title="集团重点工作督导平台 API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if active_settings.ENV.value != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if active_settings.ENV.value != "production" else None,
    )
    app.state.settings = active_settings
    app.middleware("http")(request_id_middleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(meta_router)
    app.include_router(auth_router)
    app.include_router(files_router)
    app.include_router(home_router)
    app.include_router(report_router)
    return app


app = create_app()
