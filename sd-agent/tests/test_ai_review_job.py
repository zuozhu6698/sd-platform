from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from sd_agent.adapters.teable import TeableAdapterError, TeableRecord
from sd_agent.ai import AiPipelineError, ReviewExecution, ReviewInput, ReviewResult
from sd_agent.jobs.ai_review import (
    AiReviewHandler,
    ReviewCandidate,
    TeableReviewResultSink,
    TeableReviewSource,
)

NOW = datetime(2026, 7, 24, 4, tzinfo=UTC)


def candidate(log_id: int) -> ReviewCandidate:
    return ReviewCandidate(
        f"rec-{log_id}",
        ReviewInput(
            101,
            log_id,
            "完成设备安装",
            "安装全部设备",
            date(2026, 9, 30),
            "推进中",
            60,
            (),
            "本周完成安装 3 台",
            (),
        ),
    )


def result(*, flagged: bool = False) -> ReviewResult:
    return ReviewResult(
        result="标记" if flagged else "通过",
        flags=("量化缺失",) if flagged else (),
        evidence="缺少完成数量" if flagged else "已列明完成数量",
        confidence=0.8,
        question="请补充完成数量" if flagged else "",
        source_ids=("T101", "L202"),
    )


class FakeSource:
    def __init__(self, values: tuple[ReviewCandidate, ...]) -> None:
        self.values = values
        self.calls: list[tuple[datetime, int]] = []

    async def load(self, *, scheduled_for: datetime, limit: int) -> tuple[ReviewCandidate, ...]:
        self.calls.append((scheduled_for, limit))
        return self.values


class FakeSink:
    def __init__(self) -> None:
        self.saved: list[tuple[int, str, str]] = []

    async def save(
        self,
        *,
        candidate: ReviewCandidate,
        result: ReviewResult,
        ai_run_id: str,
    ) -> None:
        self.saved.append((candidate.value.log_id, result.result, ai_run_id))


class FakePipeline:
    async def review_with_trace(self, value: ReviewInput, *, now: datetime) -> ReviewExecution:
        assert now.tzinfo is UTC
        if value.log_id == 203:
            raise AiPipelineError("LLM_UNAVAILABLE")
        return ReviewExecution(result(flagged=value.log_id == 202), f"run-{value.log_id}")


async def test_handler_reviews_each_candidate_and_continues_safe_ai_failures() -> None:
    source = FakeSource((candidate(201), candidate(202), candidate(203)))
    sink = FakeSink()
    handler = AiReviewHandler(  # type: ignore[arg-type]
        source=source,
        sink=sink,
        pipeline=FakePipeline(),
        batch_size=20,
    )

    outcome = await handler(NOW)

    assert outcome.counts == {"candidates": 3, "reviewed": 2, "flagged": 1, "failed": 1}
    assert sink.saved == [(201, "通过", "run-201"), (202, "标记", "run-202")]
    assert source.calls == [(NOW, 20)]


def test_handler_rejects_invalid_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        AiReviewHandler(  # type: ignore[arg-type]
            source=FakeSource(()), sink=FakeSink(), pipeline=FakePipeline(), batch_size=0
        )


class FakeTeable:
    def __init__(self, records: dict[str, list[TeableRecord]]) -> None:
        self.records = records
        self.updates: list[dict[str, object]] = []
        self.calls: list[tuple[str, int]] = []

    async def list_records(
        self,
        table: str,
        *,
        projection: tuple[str, ...],
        take: int,
        skip: int,
    ) -> list[TeableRecord]:
        del projection, take
        self.calls.append((table, skip))
        return self.records.get(table, [])[skip : skip + 1000]

    async def update_record(self, table: str, **kwargs: object) -> TeableRecord:
        self.updates.append({"table": table, **kwargs})
        return TeableRecord(id=str(kwargs["record_id"]), fields=dict(kwargs["fields"]))  # type: ignore[arg-type]


def row(record_id: str, **fields: Any) -> TeableRecord:
    return TeableRecord(id=record_id, fields=fields)


def source_records() -> dict[str, list[TeableRecord]]:
    return {
        "task": [
            row(
                "task-rec",
                task_id=101,
                kw_id=1,
                content="安装全部设备",
                deadline="2026-09-30",
                status="推进中",
                progress=60,
            )
        ],
        "key_work": [row("kw-rec", kw_id=1, goal="完成设备安装")],
        "progress_log": [
            row(
                "old",
                log_id=201,
                task_id=101,
                report_date="2026-07-17",
                submitted_at="2026-07-17T12:00:00+08:00",
                content="完成安装 2 台",
                progress=50,
                attachments=[],
                ai_run_id="existing-run",
            ),
            row(
                "current",
                log_id=202,
                task_id=101,
                report_date="2026-07-24",
                submitted_at="2026-07-24T11:00:00+08:00",
                content="完成安装 3 台",
                progress=60,
                attachments=["现场照片.jpg"],
                ai_run_id=None,
            ),
            row(
                "future",
                log_id=203,
                task_id=101,
                report_date="2026-07-25",
                submitted_at="2026-07-25T11:00:00+08:00",
                content="未来填报",
                progress=70,
                attachments=[],
                ai_run_id=None,
            ),
        ],
    }


async def test_teable_source_joins_unreviewed_logs_with_last_history() -> None:
    teable = FakeTeable(source_records())

    values = await TeableReviewSource(teable).load(
        scheduled_for=datetime(2026, 7, 24, 12, 30, tzinfo=UTC), limit=200
    )

    assert len(values) == 1
    assert values[0].record_id == "current"
    assert values[0].value.log_id == 202
    assert values[0].value.histories[0].log_id == 201
    assert values[0].value.attachment_names == ("现场照片.jpg",)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda records: records["task"].append(records["task"][0]), "TEABLE_TASK_CONFLICT"),
        (lambda records: records.update({"key_work": []}), "TEABLE_KEY_WORK_MISSING"),
        (
            lambda records: records["progress_log"].append(records["progress_log"][1]),
            "TEABLE_PROGRESS_CONFLICT",
        ),
        (
            lambda records: records["progress_log"][1].fields.update(task_id=999),
            "TEABLE_TASK_MISSING",
        ),
    ],
)
async def test_teable_source_fails_closed_on_join_conflicts(mutate: Any, code: str) -> None:
    records = source_records()
    mutate(records)
    with pytest.raises(TeableAdapterError) as caught:
        await TeableReviewSource(FakeTeable(records)).load(
            scheduled_for=datetime(2026, 7, 24, 12, 30, tzinfo=UTC), limit=200
        )
    assert caught.value.code == code


async def test_teable_source_rejects_invalid_response() -> None:
    records = source_records()
    records["task"][0].fields["deadline"] = "not-a-date"
    with pytest.raises(TeableAdapterError, match="TEABLE_INVALID_RESPONSE"):
        await TeableReviewSource(FakeTeable(records)).load(scheduled_for=NOW, limit=200)


async def test_teable_source_paginates_complete_snapshots() -> None:
    records = source_records()
    records["task"] = records["task"] * 1001
    teable = FakeTeable(records)
    with pytest.raises(TeableAdapterError, match="TEABLE_TASK_CONFLICT"):
        await TeableReviewSource(teable).load(scheduled_for=NOW, limit=1)
    assert ("task", 1000) in teable.calls


async def test_teable_sink_writes_structured_comment_and_review_state() -> None:
    teable = FakeTeable({})
    sink = TeableReviewResultSink(teable)  # type: ignore[arg-type]

    await sink.save(candidate=candidate(202), result=result(flagged=True), ai_run_id="run-202")

    update = teable.updates[0]
    assert update["table"] == "progress_log"
    assert update["record_id"] == "rec-202"
    assert update["idempotency_key"] == "ai-review:202:run-202"
    fields = update["fields"]
    assert isinstance(fields, dict)
    assert fields["review_status"] == "pending_confirmation"
    assert '"confidence":0.8' in str(fields["ai_comment"])


async def test_teable_sink_marks_pass_without_question() -> None:
    teable = FakeTeable({})
    await TeableReviewResultSink(teable).save(  # type: ignore[arg-type]
        candidate=candidate(201), result=result(), ai_run_id="run-201"
    )
    fields = teable.updates[0]["fields"]
    assert isinstance(fields, dict)
    assert fields["review_status"] == "ai_passed"
    assert fields["ai_question"] == ""
