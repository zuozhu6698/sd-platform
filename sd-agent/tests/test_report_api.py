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
from sd_agent.submission import SubmissionService
from tests.test_auth_service import FakeIdentities, FakeSessions
from tests.test_submission_service import FakeGateway, FakePersistence

IDEMPOTENCY_KEY = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
async def report_client() -> AsyncIterator[
    tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI]
]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_report", now=now)
    sessions = FakeSessions(
        SessionState("sid_report", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "张三", 10, True, 3)
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    persistence = FakePersistence()
    gateway = FakeGateway()
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        app.state.resources.submission = SubmissionService(
            persistence=persistence,
            gateway=gateway,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            client.cookies.set("sd_token", token)
            yield client, persistence, gateway, app


async def csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/me")
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "task_id": 101,
        "content": "本周完成接口联调并形成测试记录",
        "progress": 65,
        "file_ids": [],
        "on_behalf_of": None,
        "task_revision": 7,
    }
    values.update(overrides)
    return values


async def test_report_submit_returns_contract_and_commits(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, persistence, _, _ = report_client
    csrf_token = await csrf(client)
    response = await client.post(
        "/api/report/submit",
        json=payload(),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 201
    assert response.json()["data"]["state"] == "committed"
    assert response.json()["data"]["log_id"] == 9001
    assert persistence.completed is not None


async def test_report_submit_requires_idempotency_key(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, _, _ = report_client
    response = await client.post("/api/report/submit", json=payload())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_report_submit_requires_csrf(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, _, _ = report_client
    response = await client.post(
        "/api/report/submit",
        json=payload(),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_report_submit_maps_business_validation_error(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, _, _ = report_client
    csrf_token = await csrf(client)
    response = await client.post(
        "/api/report/submit",
        json=payload(content="太短"),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPORT_TOO_SHORT"


async def test_report_submit_is_honest_when_service_unconfigured(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, _, app = report_client
    csrf_token = await csrf(client)
    app.state.resources.submission = None
    response = await client.post(
        "/api/report/submit",
        json=payload(),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_UNAVAILABLE"


async def test_report_submit_rejects_extra_client_identity_fields(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, _, _ = report_client
    response = await client.post(
        "/api/report/submit",
        json=payload(reporter_id=999),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


async def test_report_submit_maps_teable_failure_without_details(
    report_client: tuple[httpx.AsyncClient, FakePersistence, FakeGateway, FastAPI],
) -> None:
    client, _, gateway, _ = report_client

    async def unavailable(_task_id: int) -> None:
        raise TeableAdapterError("TEABLE_UNAVAILABLE", retryable=True)

    gateway.get_task = unavailable  # type: ignore[method-assign,assignment]
    csrf_token = await csrf(client)
    response = await client.post(
        "/api/report/submit",
        json=payload(),
        headers={"Idempotency-Key": IDEMPOTENCY_KEY, "X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "TEABLE_UNAVAILABLE",
        "message": "外部数据服务暂不可用",
        "details": {},
    }
