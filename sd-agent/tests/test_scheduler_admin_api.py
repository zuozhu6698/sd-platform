from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from sd_agent.api_main import create_app
from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import AuthService, PersonIdentity, RoleScope, SessionState
from sd_agent.config import Settings
from sd_agent.scheduler.admin import (
    JobRunSummary,
    SchedulerAdminError,
    SchedulerAdminService,
    TriggerResult,
)
from tests.test_auth_service import FakeIdentities, FakeSessions

KEY = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"


class FakeRepository:
    async def list_runs(self, **_kwargs: object) -> tuple[tuple[JobRunSummary, ...], None]:
        now = datetime(2026, 7, 24, tzinfo=UTC)
        return ((JobRunSummary(RUN_ID, "urge_scan", now, "failed", {}, "FAILED", now, now)),), None

    async def enqueue_trigger(self, **kwargs: object) -> TriggerResult:
        now = kwargs["now"]
        assert isinstance(now, datetime)
        return TriggerResult(
            "33333333-3333-4333-8333-333333333333",
            "44444444-4444-4444-8444-444444444444",
            str(kwargs["job"]),
            now,
            kwargs["retry_of_job_run_id"],  # type: ignore[arg-type]
            "queued",
            False,
        )


@pytest.fixture
async def admin_client() -> AsyncIterator[tuple[httpx.AsyncClient, FakeIdentities, FastAPI]]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_admin", now=now)
    sessions = FakeSessions(
        SessionState("sid_admin", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "运维管理员", 10, True, 3)
    identities.roles = (RoleScope("ops_admin", None),)
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        app.state.resources.scheduler_admin = SchedulerAdminService(FakeRepository())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            client.cookies.set("sd_token", token)
            yield client, identities, app


async def csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/me")
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


async def test_list_runs_returns_safe_run_metadata(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client

    response = await client.get("/api/admin/runs")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["job_run_id"] == RUN_ID
    assert response.json()["data"]["items"][0]["error_code"] == "FAILED"


async def test_trigger_requires_idempotency_and_csrf(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client

    missing_key = await client.post("/api/admin/runs/urge_scan/trigger", json={})
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    missing_csrf = await client.post(
        "/api/admin/runs/urge_scan/trigger",
        json={},
        headers={"Idempotency-Key": KEY},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"


async def test_trigger_queues_manual_run_or_failed_retry(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client
    token = await csrf(client)

    response = await client.post(
        "/api/admin/runs/urge_scan/trigger",
        json={"retry_of_job_run_id": RUN_ID},
        headers={"Idempotency-Key": KEY, "X-CSRF-Token": token},
    )

    assert response.status_code == 202
    assert response.json()["data"]["state"] == "queued"
    assert response.json()["data"]["retry_of_job_run_id"] == RUN_ID


async def test_wrong_role_and_unavailable_service_are_explicit(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, identities, app = admin_client
    identities.roles = (RoleScope("leader", None),)
    forbidden = await client.get("/api/admin/runs")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "JOB_RUN_FORBIDDEN"

    identities.roles = (RoleScope("ops_admin", None),)
    app.state.resources.scheduler_admin = None
    unavailable = await client.get("/api/admin/runs")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "SCHEDULER_ADMIN_UNAVAILABLE"


class FailingRepository(FakeRepository):
    async def enqueue_trigger(self, **_kwargs: object) -> TriggerResult:
        raise SchedulerAdminError("JOB_RUN_NOT_RETRYABLE", "不可重跑", 409)


async def test_trigger_maps_domain_error(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, app = admin_client
    app.state.resources.scheduler_admin = SchedulerAdminService(FailingRepository())
    token = await csrf(client)

    response = await client.post(
        "/api/admin/runs/urge_scan/trigger",
        json={"retry_of_job_run_id": RUN_ID},
        headers={"Idempotency-Key": KEY, "X-CSRF-Token": token},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_RUN_NOT_RETRYABLE"
