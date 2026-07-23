"""增加受控附件元数据与扫描状态。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0002"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_object",
        sa.Column("file_id", sa.String(length=160), nullable=False),
        sa.Column("owner_person_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("scan_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("file_id"),
        sa.UniqueConstraint("storage_key", name="uq_file_object_storage_key"),
        schema="sd_app",
    )
    op.create_index(
        "ix_file_object_owner_state",
        "file_object",
        ["owner_person_id", "state"],
        schema="sd_app",
    )
    op.create_index(
        "ix_file_object_task",
        "file_object",
        ["task_id", "bound_at"],
        schema="sd_app",
    )


def downgrade() -> None:
    op.drop_table("file_object", schema="sd_app")
