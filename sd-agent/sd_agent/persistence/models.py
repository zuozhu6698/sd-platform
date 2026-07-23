from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = {"schema": "sd_app"}

    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    person_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    kid: Mapped[str] = mapped_column(String(32), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubmissionCommand(Base, TimestampMixin):
    __tablename__ = "submission_command"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_submission_command_idempotency"),
        Index("ix_submission_command_reconcile", "state", "updated_at"),
        {"schema": "sd_app"},
    )

    command_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    person_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    teable_record_id: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipt"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        {"schema": "sd_app"},
    )

    receipt_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)


class FileObject(Base):
    __tablename__ = "file_object"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_file_object_storage_key"),
        Index("ix_file_object_owner_state", "owner_person_id", "state"),
        Index("ix_file_object_task", "task_id", "bound_at"),
        {"schema": "sd_app"},
    )

    file_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    owner_person_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    scan_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_target_created", "target_type", "target_id", "created_at"),
        Index("ix_audit_event_who_created", "who", "created_at"),
        {"schema": "sd_app"},
    )

    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    who: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str | None] = mapped_column(String(64))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    what: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRun(Base):
    __tablename__ = "job_run"
    __table_args__ = (
        UniqueConstraint("job", "scheduled_for", name="uq_job_run_schedule"),
        {"schema": "sd_app"},
    )

    job_run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    job: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxMessage(Base, TimestampMixin):
    __tablename__ = "outbox_message"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_outbox_message_dedup"),
        Index("ix_outbox_claim", "state", "available_at"),
        {"schema": "sd_app"},
    )

    outbox_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class OutboxAttempt(Base):
    __tablename__ = "outbox_attempt"
    __table_args__ = {"schema": "sd_app"}

    attempt_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    outbox_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sd_app.outbox_message.outbox_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    redacted_error: Mapped[str | None] = mapped_column(String(512))


class ReportVersion(Base):
    __tablename__ = "report_version"
    __table_args__ = (
        UniqueConstraint("period", "audience", "revision", name="uq_report_version_revision"),
        {"schema": "sd_app"},
    )

    report_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[int | None] = mapped_column(BigInteger)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiRun(Base):
    __tablename__ = "ai_run"
    __table_args__ = {"schema": "sd_app"}

    ai_run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    review_result: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WeekSnapshot(Base):
    __tablename__ = "week_snapshot"
    __table_args__ = (
        UniqueConstraint("week_start", "scope_key", name="uq_week_snapshot_scope"),
        {"schema": "bi"},
    )

    snapshot_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
