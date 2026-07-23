"""创建 sd_app 与 bi 基线对象。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS sd_app")
    op.execute("CREATE SCHEMA IF NOT EXISTS bi")

    op.create_table(
        "auth_session",
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("kid", sa.String(length=32), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sid"),
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_auth_session_person_id",
        "auth_session",
        ["person_id"],
        schema="sd_app",
    )

    op.create_table(
        "submission_command",
        sa.Column("command_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("teable_record_id", sa.String(length=128), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_submission_command_idempotency",
        ),
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_submission_command_person_id",
        "submission_command",
        ["person_id"],
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_submission_command_task_id",
        "submission_command",
        ["task_id"],
        schema="sd_app",
    )
    op.create_index(
        "ix_submission_command_reconcile",
        "submission_command",
        ["state", "updated_at"],
        schema="sd_app",
    )

    op.create_table(
        "webhook_receipt",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        schema="sd_app",
    )

    op.create_table(
        "audit_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("who", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("what", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_audit_event_request_id",
        "audit_event",
        ["request_id"],
        schema="sd_app",
    )
    op.create_index(
        "ix_audit_event_target_created",
        "audit_event",
        ["target_type", "target_id", "created_at"],
        schema="sd_app",
    )
    op.create_index(
        "ix_audit_event_who_created",
        "audit_event",
        ["who", "created_at"],
        schema="sd_app",
    )

    op.create_table(
        "job_run",
        sa.Column("job_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("job", sa.String(length=64), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_run_id"),
        sa.UniqueConstraint("job", "scheduled_for", name="uq_job_run_schedule"),
        schema="sd_app",
    )

    op.create_table(
        "outbox_message",
        sa.Column("outbox_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.UniqueConstraint("dedup_key", name="uq_outbox_message_dedup"),
        schema="sd_app",
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox_message",
        ["state", "available_at"],
        schema="sd_app",
    )

    op.create_table(
        "outbox_attempt",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("outbox_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("redacted_error", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["sd_app.outbox_message.outbox_id"],
            name="fk_outbox_attempt_outbox_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_outbox_attempt_outbox_id",
        "outbox_attempt",
        ["outbox_id"],
        schema="sd_app",
    )

    op.create_table(
        "report_version",
        sa.Column("report_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("audience", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.BigInteger(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("report_id"),
        sa.UniqueConstraint(
            "period",
            "audience",
            "revision",
            name="uq_report_version_revision",
        ),
        schema="sd_app",
    )

    op.create_table(
        "ai_run",
        sa.Column("ai_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schema_valid", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("review_result", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ai_run_id"),
        schema="sd_app",
    )

    op.create_table(
        "week_snapshot",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("week_start", "scope_key", name="uq_week_snapshot_scope"),
        schema="bi",
    )


def downgrade() -> None:
    op.drop_table("week_snapshot", schema="bi")
    op.drop_table("ai_run", schema="sd_app")
    op.drop_table("report_version", schema="sd_app")
    op.drop_table("outbox_attempt", schema="sd_app")
    op.drop_table("outbox_message", schema="sd_app")
    op.drop_table("job_run", schema="sd_app")
    op.drop_table("audit_event", schema="sd_app")
    op.drop_table("webhook_receipt", schema="sd_app")
    op.drop_table("submission_command", schema="sd_app")
    op.drop_table("auth_session", schema="sd_app")
    op.execute("DROP SCHEMA IF EXISTS bi")
    op.execute("DROP SCHEMA IF EXISTS sd_app")
