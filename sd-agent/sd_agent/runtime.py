from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sd_agent.adapters.files import HttpFileScanner, LocalObjectStore
from sd_agent.adapters.identity import TeableIdentityStore
from sd_agent.adapters.submission import TeableSubmissionGateway
from sd_agent.adapters.teable import TeableClient
from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import AuthService
from sd_agent.config import Environment, Settings
from sd_agent.files import FileService
from sd_agent.oa import (
    MockOaGateway,
    OaOutboxHandler,
    OaUrgeOutboxHandler,
    TeableUrgeReceiptStore,
)
from sd_agent.outbox import HandlerOutboxDispatcher, OutboxHandler, OutboxProcessor
from sd_agent.outbox.admin import OutboxAdminService
from sd_agent.persistence.files import SqlFileRepository
from sd_agent.persistence.outbox import SqlOutboxRepository
from sd_agent.persistence.outbox_admin import SqlOutboxAdminRepository
from sd_agent.persistence.scheduler import SqlJobRunRepository
from sd_agent.persistence.scheduler_admin import SqlSchedulerAdminRepository
from sd_agent.persistence.sessions import SqlSessionStore
from sd_agent.persistence.sso import SqlSsoPersistence
from sd_agent.persistence.submissions import SqlSubmissionPersistence
from sd_agent.scheduler.admin import SchedulerAdminService, SchedulerTriggerOutboxHandler
from sd_agent.scheduler.catalog import JOB_SPECS, catalog_hash
from sd_agent.scheduler.service import JobHandler, SchedulerService
from sd_agent.sso import MockSsoProvider, SsoService
from sd_agent.submission import SubmissionService
from sd_agent.tasks import MyTasksService


@dataclass(slots=True)
class RuntimeResources:
    settings: Settings
    engine: AsyncEngine | None
    http: httpx.AsyncClient
    teable: TeableClient | None
    auth: AuthService | None
    sso: SsoService | None
    submission: SubmissionService | None
    my_tasks: MyTasksService | None
    files: FileService | None
    outbox: OutboxProcessor | None
    outbox_admin: OutboxAdminService | None
    scheduler: SchedulerService | None
    scheduler_admin: SchedulerAdminService | None

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        outbox_handlers: Mapping[str, OutboxHandler] | None = None,
        job_handlers: Mapping[str, JobHandler] | None = None,
    ) -> RuntimeResources:
        jobs = dict(job_handlers or {})
        required_jobs = {spec.name for spec in JOB_SPECS}
        if jobs and set(jobs) != required_jobs:
            missing = sorted(required_jobs - set(jobs))
            unknown = sorted(set(jobs) - required_jobs)
            raise ValueError(f"job handler registry mismatch: missing={missing}, unknown={unknown}")
        offline_oa_kinds = {"oa.complete_pending", "oa.send_urge"}
        duplicate_oa_kinds = offline_oa_kinds & set(outbox_handlers or {})
        if settings.OA_MODE == "mock" and duplicate_oa_kinds:
            raise ValueError(f"duplicate offline OA handlers: {sorted(duplicate_oa_kinds)}")
        database_url = settings.SD_APP_DATABASE_URL.get_secret_value()
        engine = (
            create_async_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
            if database_url
            else None
        )
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3, read=5, write=5, pool=3),
            follow_redirects=False,
        )
        token = settings.TEABLE_TOKEN.get_secret_value()
        teable = (
            TeableClient(
                base_url=settings.TEABLE_BASE_URL,
                token=token,
                table_ids=settings.TEABLE_TABLE_IDS,
                http=http,
            )
            if settings.TEABLE_BASE_URL and token and settings.TEABLE_TABLE_IDS
            else None
        )
        jwt_secret = settings.JWT_SECRET_V1.get_secret_value()
        csrf_secret = settings.CSRF_SECRET.get_secret_value()
        identities = TeableIdentityStore(teable) if teable is not None else None
        tokens = (
            TokenService(
                active_kid=settings.JWT_ACTIVE_KID,
                keys={"v1": jwt_secret},
                expire_minutes=settings.JWT_EXPIRE_MINUTES,
            )
            if jwt_secret
            else None
        )
        auth = (
            AuthService(
                tokens=tokens,
                csrf=CsrfProtector(csrf_secret),
                sessions=SqlSessionStore(engine),
                identities=identities,
            )
            if engine is not None and identities is not None and tokens is not None and csrf_secret
            else None
        )
        sso = (
            SsoService(
                provider=MockSsoProvider(person_id=settings.SSO_STUB_PERSON_ID),
                persistence=SqlSsoPersistence(engine),
                identities=identities,
                tokens=tokens,
                allowed_redirect_paths=settings.ALLOWED_REDIRECT_PATHS,
            )
            if settings.SSO_MODE == "stub"
            and engine is not None
            and identities is not None
            and tokens is not None
            else None
        )
        submission = (
            SubmissionService(
                persistence=SqlSubmissionPersistence(engine),
                gateway=TeableSubmissionGateway(teable=teable, engine=engine),
            )
            if engine is not None and teable is not None
            else None
        )
        my_tasks = MyTasksService(teable) if teable is not None else None
        files = (
            FileService(
                scanner=HttpFileScanner(base_url=settings.FILE_SCAN_BASE_URL, http=http),
                store=LocalObjectStore(Path(settings.FILE_STORAGE_ROOT)),
                repository=SqlFileRepository(engine),
                max_bytes=settings.FILE_MAX_MB * 1024 * 1024,
            )
            if engine is not None and settings.FILE_SCAN_BASE_URL and settings.FILE_STORAGE_ROOT
            else None
        )
        handlers = dict(outbox_handlers or {})
        if settings.OA_MODE == "mock":
            oa_gateway = MockOaGateway()
            handlers["oa.complete_pending"] = OaOutboxHandler(oa_gateway)
            if teable is not None:
                handlers["oa.send_urge"] = OaUrgeOutboxHandler(
                    oa_gateway,
                    TeableUrgeReceiptStore(teable),
                )
        scheduler = (
            SchedulerService(
                SqlJobRunRepository(engine),
                handlers=jobs,
                config_hash=catalog_hash(),
            )
            if engine is not None and jobs
            else None
        )
        scheduler_admin = (
            SchedulerAdminService(SqlSchedulerAdminRepository(engine))
            if engine is not None
            else None
        )
        if scheduler is not None:
            handlers["scheduler.run_job"] = SchedulerTriggerOutboxHandler(scheduler)
        outbox = (
            OutboxProcessor(
                repository=SqlOutboxRepository(engine),
                dispatcher=HandlerOutboxDispatcher(handlers),
                max_attempts=settings.OUTBOX_MAX_ATTEMPTS,
                lease_seconds=settings.OUTBOX_LEASE_SECONDS,
            )
            if engine is not None and handlers
            else None
        )
        outbox_admin = (
            OutboxAdminService(SqlOutboxAdminRepository(engine)) if engine is not None else None
        )
        return cls(
            settings=settings,
            engine=engine,
            http=http,
            teable=teable,
            auth=auth,
            sso=sso,
            submission=submission,
            my_tasks=my_tasks,
            files=files,
            outbox=outbox,
            outbox_admin=outbox_admin,
            scheduler=scheduler,
            scheduler_admin=scheduler_admin,
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
