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
from sd_agent.outbox.admin import (
    DeadLetterSummary,
    OutboxAdminError,
    OutboxAdminService,
    ReplayApproval,
    ReplayResult,
)
from tests.test_auth_service import FakeIdentities, FakeSessions

APPROVAL_KEY = "11111111-1111-4111-8111-111111111111"
EXECUTION_KEY = "22222222-2222-4222-8222-222222222222"
OUTBOX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
APPROVAL_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FakeAdminRepository:
    async def list_dead_letters(
        self,
        **_kwargs: object,
    ) -> tuple[tuple[DeadLetterSummary, ...], None]:
        now = datetime(2026, 7, 24, tzinfo=UTC)
        return (
            (
                DeadLetterSummary(
                    OUTBOX_ID,
                    "oa.complete_pending",
                    "dead_letter",
                    6,
                    "OA_UNAVAILABLE",
                    now,
                    now,
                ),
            ),
            None,
        )

    async def approve_replay(self, **_kwargs: object) -> ReplayApproval:
        return ReplayApproval(
            APPROVAL_ID,
            OUTBOX_ID,
            7,
            datetime(2026, 7, 24, tzinfo=UTC),
            False,
        )

    async def replay(self, **_kwargs: object) -> ReplayResult:
        return ReplayResult(OUTBOX_ID, APPROVAL_ID, "retry", False)


@pytest.fixture
async def admin_client() -> AsyncIterator[tuple[httpx.AsyncClient, FakeIdentities, FastAPI]]:
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = TokenService(active_kid="v1", keys={"v1": "j" * 32}, expire_minutes=60)
    token = tokens.issue(person_id=7, sid="sid_admin", now=now)
    sessions = FakeSessions(
        SessionState("sid_admin", 7, "v1", "old", now + timedelta(minutes=60), None)
    )
    identities = FakeIdentities()
    identities.person = PersonIdentity(7, "督导管理员", 10, True, 3)
    identities.roles = (RoleScope("supervision_admin", None),)
    auth = AuthService(
        tokens=tokens,
        csrf=CsrfProtector("c" * 32),
        sessions=sessions,
        identities=identities,
    )
    app = create_app(Settings(_env_file=None, ENV="test"))
    async with app.router.lifespan_context(app):
        app.state.resources.auth = auth
        app.state.resources.outbox_admin = OutboxAdminService(FakeAdminRepository())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            client.cookies.set("sd_token", token)
            yield client, identities, app


async def csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/me")
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


async def test_list_dead_letters_returns_only_safe_metadata(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client

    response = await client.get("/api/admin/outbox/dead-letters")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["outbox_id"] == OUTBOX_ID
    assert item["attempt_count"] == 6
    assert "payload" not in item
    assert "dedup_key" not in item


async def test_approve_requires_csrf_and_idempotency(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client

    missing = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay-approvals",
        json={"reason": "OA 服务恢复后经人工核对允许补发"},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    no_csrf = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay-approvals",
        json={"reason": "OA 服务恢复后经人工核对允许补发"},
        headers={"Idempotency-Key": APPROVAL_KEY},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"


async def test_supervision_admin_approves_replay(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, _ = admin_client
    csrf_token = await csrf(client)

    response = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay-approvals",
        json={"reason": "OA 服务恢复后经人工核对允许补发"},
        headers={"Idempotency-Key": APPROVAL_KEY, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 201
    assert response.json()["data"]["approval_id"] == APPROVAL_ID


async def test_ops_admin_executes_approved_replay(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, identities, _ = admin_client
    identities.roles = (RoleScope("ops_admin", None),)
    csrf_token = await csrf(client)

    response = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay",
        json={"approval_id": APPROVAL_ID},
        headers={"Idempotency-Key": EXECUTION_KEY, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "outbox_id": OUTBOX_ID,
        "approval_id": APPROVAL_ID,
        "state": "retry",
        "idempotent": False,
    }


async def test_wrong_role_is_forbidden(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, identities, _ = admin_client
    identities.roles = (RoleScope("leader", None),)

    response = await client.get("/api/admin/outbox/dead-letters")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "OUTBOX_FORBIDDEN"


async def test_admin_service_unavailable_is_explicit(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, app = admin_client
    app.state.resources.outbox_admin = None

    response = await client.get("/api/admin/outbox/dead-letters")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OUTBOX_ADMIN_UNAVAILABLE"


class FailingAdminRepository(FakeAdminRepository):
    async def approve_replay(self, **_kwargs: object) -> ReplayApproval:
        raise OutboxAdminError("OUTBOX_NOT_DEAD", "消息不处于死信状态", 409)

    async def replay(self, **_kwargs: object) -> ReplayResult:
        raise OutboxAdminError("OUTBOX_APPROVAL_CONSUMED", "补发审批已使用", 409)


async def test_approve_maps_domain_error(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, _, app = admin_client
    app.state.resources.outbox_admin = OutboxAdminService(FailingAdminRepository())
    csrf_token = await csrf(client)

    response = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay-approvals",
        json={"reason": "OA 服务恢复后经人工核对允许补发"},
        headers={"Idempotency-Key": APPROVAL_KEY, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OUTBOX_NOT_DEAD"


async def test_replay_requires_idempotency_and_maps_domain_error(
    admin_client: tuple[httpx.AsyncClient, FakeIdentities, FastAPI],
) -> None:
    client, identities, app = admin_client
    identities.roles = (RoleScope("ops_admin", None),)
    missing = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay",
        json={"approval_id": APPROVAL_ID},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    app.state.resources.outbox_admin = OutboxAdminService(FailingAdminRepository())
    csrf_token = await csrf(client)
    response = await client.post(
        f"/api/admin/outbox/{OUTBOX_ID}/replay",
        json={"approval_id": APPROVAL_ID},
        headers={"Idempotency-Key": EXECUTION_KEY, "X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OUTBOX_APPROVAL_CONSUMED"
