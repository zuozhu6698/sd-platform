"""增加计划任务幂等手动触发与失败重跑请求。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0005"
down_revision: str | None = "20260724_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_trigger_request",
        sa.Column("trigger_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("job", sa.String(length=64), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_of_job_run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("outbox_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["retry_of_job_run_id"],
            ["sd_app.job_run.job_run_id"],
            name="fk_job_trigger_request_retry_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["sd_app.outbox_message.outbox_id"],
            name="fk_job_trigger_request_outbox",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("trigger_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_job_trigger_idempotency"),
        sa.UniqueConstraint("retry_of_job_run_id", name="uq_job_trigger_retry_run"),
        sa.UniqueConstraint("outbox_id", name="uq_job_trigger_outbox"),
        schema="sd_app",
    )
    op.create_index(
        "ix_job_trigger_created", "job_trigger_request", ["created_at"], schema="sd_app"
    )


def downgrade() -> None:
    op.drop_table("job_trigger_request", schema="sd_app")
