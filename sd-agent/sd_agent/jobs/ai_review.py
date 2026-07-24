from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableRecord
from sd_agent.ai import (
    AiPipelineError,
    AiPipelineService,
    ProgressFact,
    ReviewInput,
    ReviewResult,
)
from sd_agent.scheduler.service import JobOutcome


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    record_id: str
    value: ReviewInput


class ReviewSource(Protocol):
    async def load(self, *, scheduled_for: datetime, limit: int) -> tuple[ReviewCandidate, ...]: ...


class ReviewResultSink(Protocol):
    async def save(
        self,
        *,
        candidate: ReviewCandidate,
        result: ReviewResult,
        ai_run_id: str,
    ) -> None: ...


class AiReviewHandler:
    def __init__(
        self,
        *,
        source: ReviewSource,
        sink: ReviewResultSink,
        pipeline: AiPipelineService,
        batch_size: int = 200,
    ) -> None:
        if not 1 <= batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        self._source = source
        self._sink = sink
        self._pipeline = pipeline
        self._batch_size = batch_size

    async def __call__(self, scheduled_for: datetime) -> JobOutcome:
        candidates = await self._source.load(
            scheduled_for=scheduled_for,
            limit=self._batch_size,
        )
        reviewed = 0
        flagged = 0
        failed = 0
        for candidate in candidates:
            try:
                execution = await self._pipeline.review_with_trace(
                    candidate.value,
                    now=scheduled_for.astimezone(UTC),
                )
            except AiPipelineError:
                failed += 1
                continue
            await self._sink.save(
                candidate=candidate,
                result=execution.result,
                ai_run_id=execution.ai_run_id,
            )
            reviewed += 1
            flagged += int(execution.result.result == "标记")
        return JobOutcome(
            {
                "candidates": len(candidates),
                "reviewed": reviewed,
                "flagged": flagged,
                "failed": failed,
            }
        )


class _TaskFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: int
    kw_id: int
    content: str
    deadline: date
    status: str
    progress: int


class _KeyWorkFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kw_id: int
    goal: str


class _ProgressFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    log_id: int
    task_id: int
    report_date: date
    submitted_at: datetime
    content: str
    progress: int
    attachments: list[str] = Field(default_factory=list)
    ai_run_id: str | None = None


class TeableReviewSource:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def load(self, *, scheduled_for: datetime, limit: int) -> tuple[ReviewCandidate, ...]:
        tasks = [
            _validated(_TaskFields, row.fields)
            for row in await _all_records(
                self._teable,
                "task",
                ("task_id", "kw_id", "content", "deadline", "status", "progress"),
            )
        ]
        key_works = [
            _validated(_KeyWorkFields, row.fields)
            for row in await _all_records(self._teable, "key_work", ("kw_id", "goal"))
        ]
        progress_rows = await _all_records(
            self._teable,
            "progress_log",
            (
                "log_id",
                "task_id",
                "report_date",
                "submitted_at",
                "content",
                "progress",
                "attachments",
                "ai_run_id",
            ),
        )
        progress = [(_validated(_ProgressFields, row.fields), row.id) for row in progress_rows]
        task_by_id = _unique_by(tasks, "task_id", "TEABLE_TASK_CONFLICT")
        key_work_by_id = _unique_by(key_works, "kw_id", "TEABLE_KEY_WORK_CONFLICT")
        log_ids = [item.log_id for item, _record_id in progress]
        if len(log_ids) != len(set(log_ids)):
            raise TeableAdapterError("TEABLE_PROGRESS_CONFLICT", retryable=False)

        candidates: list[tuple[_ProgressFields, str]] = sorted(
            (
                (item, record_id)
                for item, record_id in progress
                if item.ai_run_id is None and item.submitted_at <= scheduled_for
            ),
            key=lambda pair: (pair[0].submitted_at, pair[0].log_id),
        )[:limit]
        result: list[ReviewCandidate] = []
        for current, record_id in candidates:
            task = task_by_id.get(current.task_id)
            if task is None:
                raise TeableAdapterError("TEABLE_TASK_MISSING", retryable=False)
            key_work = key_work_by_id.get(task.kw_id)
            if key_work is None:
                raise TeableAdapterError("TEABLE_KEY_WORK_MISSING", retryable=False)
            history = tuple(
                ProgressFact(item.log_id, item.report_date, item.progress, item.content)
                for item, _ in sorted(
                    (
                        pair
                        for pair in progress
                        if pair[0].task_id == current.task_id
                        and pair[0].submitted_at < current.submitted_at
                    ),
                    key=lambda pair: (pair[0].submitted_at, pair[0].log_id),
                )[-4:]
            )
            result.append(
                ReviewCandidate(
                    record_id,
                    ReviewInput(
                        task.task_id,
                        current.log_id,
                        key_work.goal,
                        task.content,
                        task.deadline,
                        task.status,
                        current.progress,
                        history,
                        current.content,
                        tuple(current.attachments),
                    ),
                )
            )
        return tuple(result)


class TeableReviewResultSink:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def save(
        self,
        *,
        candidate: ReviewCandidate,
        result: ReviewResult,
        ai_run_id: str,
    ) -> None:
        comment = json.dumps(
            {
                "flags": [flag.value for flag in result.flags],
                "evidence": result.evidence,
                "confidence": result.confidence,
                "source_ids": list(result.source_ids),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await self._teable.update_record(
            "progress_log",
            record_id=candidate.record_id,
            fields={
                "ai_result": result.result,
                "ai_comment": comment,
                "ai_question": result.question,
                "ai_run_id": ai_run_id,
                "review_status": (
                    "pending_confirmation" if result.result == "标记" else "ai_passed"
                ),
            },
            idempotency_key=f"ai-review:{candidate.value.log_id}:{ai_run_id}",
        )


async def _all_records(
    teable: TeableClient,
    table: str,
    projection: tuple[str, ...],
) -> list[TeableRecord]:
    result: list[TeableRecord] = []
    skip = 0
    while True:
        page = await teable.list_records(table, projection=projection, take=1000, skip=skip)
        result.extend(page)
        if len(page) < 1000:
            return result
        skip += len(page)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _validated(model: type[ModelT], fields: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc


def _unique_by(
    values: list[ModelT],
    field: str,
    error_code: str,
) -> dict[int, ModelT]:
    result: dict[int, ModelT] = {}
    for value in values:
        key = getattr(value, field)
        if not isinstance(key, int) or key in result:
            raise TeableAdapterError(error_code, retryable=False)
        result[key] = value
    return result
