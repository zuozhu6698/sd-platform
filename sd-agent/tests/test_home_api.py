from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from sd_agent.adapters.teable import TeableAdapterError
from sd_agent.api_main import create_app
from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import AuthService, PersonIdentity, SessionState
from sd_agent.config import Settings
from sd_agent.tasks import MyTasksService
from tests.test_auth_service import FakeIdentities, FakeSessions
from tests.test_tasks_service import FakeTeable, owner, task


@pytest.fixture
async def home_client() -> AsyncIterator[tuple[httpx.AsyncClient, FakeTeable, FastAPI]]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_home", now=now)
    sessions = FakeSessions(
        SessionState("sid_home", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "张三", 10, True, 3)
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    teable = FakeTeable()
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        app.state.resources.my_tasks = MyTasksService(teable)  # type: ignore[arg-type]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            client.cookies.set("sd_token", token)
            yield client, teable, app


async def test_home_summary_returns_only_authenticated_person_tasks(
    home_client: tuple[httpx.AsyncClient, FakeTeable, FastAPI],
) -> None:
    client, teable, _ = home_client
    teable.owners = [owner(1), owner(2, person_id=8)]
    teable.tasks = {1: [task(1, deadline=None, ai_flag="risk")]}
    response = await client.get("/api/home/summary")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["counts"] == {"total": 1, "overdue": 0, "attention": 1}
    assert payload["tasks"][0]["task_id"] == 1


async def test_home_summary_requires_authentication(
    home_client: tuple[httpx.AsyncClient, FakeTeable, FastAPI],
) -> None:
    client, _, _ = home_client
    client.cookies.clear()
    response = await client.get("/api/home/summary")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


async def test_home_summary_is_honest_when_unconfigured(
    home_client: tuple[httpx.AsyncClient, FakeTeable, FastAPI],
) -> None:
    client, _, app = home_client
    app.state.resources.my_tasks = None
    response = await client.get("/api/home/summary")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TASKS_UNAVAILABLE"


@pytest.mark.parametrize(("retryable", "status"), [(True, 503), (False, 502)])
async def test_home_summary_maps_teable_failure(
    home_client: tuple[httpx.AsyncClient, FakeTeable, FastAPI],
    retryable: bool,
    status: int,
) -> None:
    client, _, app = home_client
    service = app.state.resources.my_tasks

    async def fail(_person_id: int, *, today: object) -> None:
        del today
        raise TeableAdapterError("TEABLE_FAILURE", retryable=retryable)

    service.list_for_person = fail  # type: ignore[method-assign,assignment]
    response = await client.get("/api/home/summary")
    assert response.status_code == status
    assert response.json()["error"]["code"] == "TEABLE_FAILURE"
