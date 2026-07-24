from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Query

from sd_agent.api_main import create_app
from sd_agent.config import Settings
from sd_agent.errors import AppError
from sd_agent.request_context import choose_request_id


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        ENV="test",
        AUTH_DEV_LOGIN=True,
        COOKIE_SECURE=False,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)

    async def validate_integer(value: int = Query()) -> dict[str, int]:
        return {"value": value}

    async def raise_app_error() -> None:
        raise AppError("TEST_CONFLICT", "测试冲突", 409, {"field": "state"})

    app.add_api_route("/api/test-validation", validate_integer)
    app.add_api_route("/api/test-app-error", raise_app_error)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as api_client:
            yield api_client


@pytest.mark.parametrize("candidate", ["req_abc-123", "A.b_9"])
def test_request_id_accepts_safe_values(candidate: str) -> None:
    assert choose_request_id(candidate) == candidate


@pytest.mark.parametrize("candidate", [None, "", "contains space", "../bad", "x" * 65])
def test_request_id_replaces_unsafe_values(candidate: str | None) -> None:
    assert choose_request_id(candidate).startswith("req_")


async def test_health_and_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "req_test"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req_test"
    assert response.json() == {
        "data": {"status": "alive"},
        "request_id": "req_test",
    }


async def test_readiness_allows_unconfigured_dependencies_outside_production(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["status"] == "ready"
    assert payload["data"]["dependencies"]["postgres"]["state"] == "not_configured"


async def test_validation_errors_use_safe_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/route-that-does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "HTTP_404"
    assert payload["request_id"].startswith("req_")


async def test_request_validation_does_not_echo_input(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/test-validation", params={"value": "secret-input"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert "secret-input" not in response.text


async def test_application_errors_keep_the_stable_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/test-app-error")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "TEST_CONFLICT",
        "message": "测试冲突",
        "details": {"field": "state"},
    }


async def test_meta_reports_effective_flags(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/meta")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["name"] == "sd-platform"
    assert payload["environment"] == "test"
    assert payload["features"] == {"dev_login": True, "scheduler": False, "outbox": False}
