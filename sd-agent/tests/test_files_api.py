from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from sd_agent.api_main import create_app
from sd_agent.auth import CsrfProtector, TokenService
from sd_agent.auth.service import AuthService, PersonIdentity, SessionState
from sd_agent.config import Settings
from sd_agent.files import FileService, ScanVerdict
from tests.test_auth_service import FakeIdentities, FakeSessions
from tests.test_file_service import FakeRepository, FakeScanner, FakeStore


@pytest.fixture
async def files_client() -> AsyncIterator[
    tuple[httpx.AsyncClient, FileService, FakeScanner, FastAPI]
]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_files", now=now)
    sessions = FakeSessions(
        SessionState("sid_files", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "张三", 10, True, 3)
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    scanner = FakeScanner()
    service = FileService(
        scanner=scanner,
        store=FakeStore(),
        repository=FakeRepository(),
        max_bytes=1024,
    )
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        app.state.resources.files = service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            client.cookies.set("sd_token", token)
            yield client, service, scanner, app


async def csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/me")
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


async def test_upload_and_owner_download_contract(
    files_client: tuple[httpx.AsyncClient, FileService, FakeScanner, FastAPI],
) -> None:
    client, _, scanner, _ = files_client
    response = await client.post(
        "/api/files",
        data={"task_id": "101"},
        files={"file": ("周报.pdf", b"%PDF-safe", "application/pdf")},
        headers={"X-CSRF-Token": await csrf(client)},
    )
    assert response.status_code == 201
    payload = response.json()["data"]
    assert payload["state"] == "clean"
    assert scanner.media_type == "application/pdf"
    downloaded = await client.get(f"/api/files/{payload['file_id']}")
    assert downloaded.status_code == 200 and downloaded.content == b"%PDF-safe"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert "UTF-8''" in downloaded.headers["content-disposition"]


async def test_upload_requires_csrf_and_configured_service(
    files_client: tuple[httpx.AsyncClient, FileService, FakeScanner, FastAPI],
) -> None:
    client, _, _, app = files_client
    response = await client.post(
        "/api/files",
        files={"file": ("safe.pdf", b"%PDF-safe", "application/pdf")},
    )
    assert response.status_code == 403
    app.state.resources.files = None
    response = await client.post(
        "/api/files",
        files={"file": ("safe.pdf", b"%PDF-safe", "application/pdf")},
        headers={"X-CSRF-Token": await csrf(client)},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "FILES_UNAVAILABLE"


async def test_upload_maps_scanner_rejection_without_signature_leak(
    files_client: tuple[httpx.AsyncClient, FileService, FakeScanner, FastAPI],
) -> None:
    client, _, scanner, _ = files_client
    scanner.verdict = ScanVerdict(False, "clam", "Sensitive-Signature")
    response = await client.post(
        "/api/files",
        files={"file": ("bad.pdf", b"%PDF-bad", "application/pdf")},
        headers={"X-CSRF-Token": await csrf(client)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILE_REJECTED"
    assert "Sensitive-Signature" not in response.text


async def test_download_hides_missing_or_unavailable_file_service(
    files_client: tuple[httpx.AsyncClient, FileService, FakeScanner, FastAPI],
) -> None:
    client, _, _, app = files_client
    response = await client.get("/api/files/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"
    app.state.resources.files = None
    response = await client.get("/api/files/missing")
    assert response.status_code == 503
