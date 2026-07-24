from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260723_0001_sd_app_baseline.py"
)
FILE_MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260723_0002_file_object.py"
)
REPLAY_MIGRATION = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "20260724_0003_outbox_replay_approval.py"
)
SSO_MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260724_0004_sso_login_attempt.py"
)
JOB_TRIGGER_MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260724_0005_job_trigger_request.py"
)


def test_baseline_migration_is_explicit_and_immutable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "sd_agent.persistence" not in source

    tree = ast.parse(source)
    tables = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "op"
        and call.func.attr == "create_table"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }
    assert tables == {
        "auth_session",
        "submission_command",
        "webhook_receipt",
        "audit_event",
        "job_run",
        "outbox_message",
        "outbox_attempt",
        "report_version",
        "ai_run",
        "week_snapshot",
    }


def test_downgrade_drops_child_before_parent() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.index('op.drop_table("outbox_attempt"') < source.index(
        'op.drop_table("outbox_message"'
    )


def test_file_migration_is_explicit_and_reversible() -> None:
    source = FILE_MIGRATION.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert 'revision: str = "20260723_0002"' in source
    assert 'down_revision: str | None = "20260723_0001"' in source
    assert 'op.create_table(\n        "file_object"' in source
    assert 'op.drop_table("file_object", schema="sd_app")' in source


def test_replay_approval_migration_is_explicit_and_reversible() -> None:
    source = REPLAY_MIGRATION.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert 'revision: str = "20260724_0003"' in source
    assert 'down_revision: str | None = "20260723_0002"' in source
    assert '"outbox_replay_approval"' in source
    assert 'postgresql_where=sa.text("consumed_at IS NULL")' in source
    assert 'op.drop_table("outbox_replay_approval", schema="sd_app")' in source


def test_sso_migration_is_explicit_and_reversible() -> None:
    source = SSO_MIGRATION.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert 'revision: str = "20260724_0004"' in source
    assert 'down_revision: str | None = "20260724_0003"' in source
    assert '"sso_login_attempt"' in source
    assert '"uq_sso_login_attempt_ticket_hash"' in source
    assert 'op.drop_table("sso_login_attempt", schema="sd_app")' in source


def test_job_trigger_migration_is_explicit_and_reversible() -> None:
    source = JOB_TRIGGER_MIGRATION.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert 'revision: str = "20260724_0005"' in source
    assert 'down_revision: str | None = "20260724_0004"' in source
    assert '"job_trigger_request"' in source
    assert '"uq_job_trigger_idempotency"' in source
    assert '"uq_job_trigger_retry_run"' in source
    assert 'op.drop_table("job_trigger_request", schema="sd_app")' in source
