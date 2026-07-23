from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260723_0001_sd_app_baseline.py"
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
