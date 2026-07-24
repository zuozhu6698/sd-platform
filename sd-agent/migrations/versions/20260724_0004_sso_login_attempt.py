"""增加 SSO 一次性 state、nonce 与 ticket 防重放记录。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0004"
down_revision: str | None = "20260724_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sso_login_attempt",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_path", sa.String(length=256), nullable=False),
        sa.Column("ticket_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint("state_hash", name="uq_sso_login_attempt_state_hash"),
        sa.UniqueConstraint("ticket_hash", name="uq_sso_login_attempt_ticket_hash"),
        schema="sd_app",
    )
    op.create_index(
        "ix_sso_login_attempt_pending",
        "sso_login_attempt",
        ["consumed_at", "expires_at"],
        schema="sd_app",
    )


def downgrade() -> None:
    op.drop_table("sso_login_attempt", schema="sd_app")
