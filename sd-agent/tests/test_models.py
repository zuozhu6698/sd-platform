from __future__ import annotations

from sd_agent.persistence.models import Base


def test_application_schema_contains_all_contract_tables() -> None:
    names = {(table.schema, table.name) for table in Base.metadata.tables.values()}
    assert names == {
        ("sd_app", "auth_session"),
        ("sd_app", "submission_command"),
        ("sd_app", "webhook_receipt"),
        ("sd_app", "file_object"),
        ("sd_app", "audit_event"),
        ("sd_app", "job_run"),
        ("sd_app", "outbox_message"),
        ("sd_app", "outbox_attempt"),
        ("sd_app", "report_version"),
        ("sd_app", "ai_run"),
        ("bi", "week_snapshot"),
    }


def test_append_only_tables_have_no_update_delete_cascade() -> None:
    audit = Base.metadata.tables["sd_app.audit_event"]
    attempts = Base.metadata.tables["sd_app.outbox_attempt"]
    assert not audit.foreign_keys
    assert {fk.ondelete for fk in attempts.foreign_keys} == {"RESTRICT"}
