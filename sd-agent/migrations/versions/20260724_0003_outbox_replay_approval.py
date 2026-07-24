"""增加 outbox 死信双人审批与幂等补发记录。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_replay_approval",
        sa.Column("approval_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("outbox_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "approval_idempotency_key",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("reason_hash", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by", sa.BigInteger(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_idempotency_key",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["sd_app.outbox_message.outbox_id"],
            name="fk_outbox_replay_approval_outbox_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint(
            "approval_idempotency_key",
            name="uq_outbox_replay_approval_idempotency",
        ),
        sa.UniqueConstraint(
            "execution_idempotency_key",
            name="uq_outbox_replay_execution_idempotency",
        ),
        schema="sd_app",
    )
    op.create_index(
        "ix_sd_app_outbox_replay_approval_outbox_id",
        "outbox_replay_approval",
        ["outbox_id"],
        schema="sd_app",
    )
    op.create_index(
        "uq_outbox_active_replay_approval",
        "outbox_replay_approval",
        ["outbox_id"],
        unique=True,
        schema="sd_app",
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("outbox_replay_approval", schema="sd_app")
