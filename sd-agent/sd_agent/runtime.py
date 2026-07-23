from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sd_agent.config import Environment, Settings


@dataclass(slots=True)
class RuntimeResources:
    settings: Settings
    engine: AsyncEngine | None
    http: httpx.AsyncClient

    @classmethod
    def create(cls, settings: Settings) -> RuntimeResources:
        database_url = settings.SD_APP_DATABASE_URL.get_secret_value()
        engine = (
            create_async_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
            if database_url
            else None
        )
        return cls(
            settings=settings,
            engine=engine,
            http=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3, read=5, write=5, pool=3),
                follow_redirects=False,
            ),
        )

    async def close(self) -> None:
        await self.http.aclose()
        if self.engine is not None:
            await self.engine.dispose()

    async def readiness(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        postgres = await self._check_postgres()
        teable = await self._check_teable()
        required_ready = postgres["required_ready"] and teable["required_ready"]
        return required_ready, {
            "postgres": postgres,
            "teable": teable,
            "oa": {"state": "pending", "required": False},
            "llm": {"state": "pending", "required": False},
        }

    async def _check_postgres(self) -> dict[str, Any]:
        if self.engine is None:
            if self.settings.ENV is Environment.PRODUCTION:
                return {"state": "unavailable", "required": True, "required_ready": False}
            return {"state": "not_configured", "required": False, "required_ready": True}
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return {"state": "unavailable", "required": True, "required_ready": False}
        return {"state": "ready", "required": True, "required_ready": True}

    async def _check_teable(self) -> dict[str, Any]:
        if not self.settings.TEABLE_BASE_URL:
            if self.settings.ENV is Environment.PRODUCTION:
                return {"state": "unavailable", "required": True, "required_ready": False}
            return {"state": "not_configured", "required": False, "required_ready": True}
        try:
            response = await self.http.get(f"{self.settings.TEABLE_BASE_URL.rstrip('/')}/health")
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return {"state": "unavailable", "required": True, "required_ready": False}
        return {"state": "ready", "required": True, "required_ready": True}
