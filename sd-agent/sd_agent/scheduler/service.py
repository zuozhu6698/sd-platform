from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class JobOutcome:
    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class JobRunClaim:
    job_run_id: str


@dataclass(frozen=True, slots=True)
class JobExecution:
    job_run_id: str | None
    job: str
    state: str
    idempotent: bool


class SchedulerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JobHandler(Protocol):
    async def __call__(self, scheduled_for: datetime) -> JobOutcome: ...


class JobRunRepository(Protocol):
    async def claim_run(
        self,
        *,
        job: str,
        scheduled_for: datetime,
        config_hash: str,
        started_at: datetime,
    ) -> JobRunClaim | None: ...

    async def finish_run(
        self,
        *,
        job_run_id: str,
        state: str,
        counts: Mapping[str, int],
        error_code: str | None,
        finished_at: datetime,
    ) -> None: ...


class SchedulerService:
    def __init__(
        self,
        repository: JobRunRepository,
        *,
        handlers: Mapping[str, JobHandler],
        config_hash: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._handlers = dict(handlers)
        self._config_hash = config_hash
        self._clock = clock or (lambda: datetime.now(SHANGHAI))

    async def run(
        self,
        job: str,
        *,
        scheduled_for: datetime,
        now: datetime,
    ) -> JobExecution:
        handler = self._handlers.get(job)
        if handler is None:
            raise SchedulerError("JOB_NOT_REGISTERED")
        _require_shanghai_time(scheduled_for)
        _require_shanghai_time(now)
        if len(self._config_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self._config_hash
        ):
            raise SchedulerError("CONFIG_HASH_INVALID")

        claim = await self._repository.claim_run(
            job=job,
            scheduled_for=scheduled_for,
            config_hash=self._config_hash,
            started_at=now,
        )
        if claim is None:
            return JobExecution(None, job, "duplicate", True)

        try:
            outcome = await handler(scheduled_for)
            counts = _validated_counts(outcome.counts)
        except Exception as exc:
            finished_at = self._clock()
            _require_shanghai_time(finished_at)
            await self._repository.finish_run(
                job_run_id=claim.job_run_id,
                state="failed",
                counts={},
                error_code="JOB_EXECUTION_FAILED",
                finished_at=finished_at,
            )
            raise SchedulerError("JOB_EXECUTION_FAILED") from exc

        finished_at = self._clock()
        _require_shanghai_time(finished_at)
        await self._repository.finish_run(
            job_run_id=claim.job_run_id,
            state="succeeded",
            counts=counts,
            error_code=None,
            finished_at=finished_at,
        )
        return JobExecution(claim.job_run_id, job, "succeeded", False)


def _require_shanghai_time(value: datetime) -> None:
    if value.tzinfo is None or getattr(value.tzinfo, "key", None) != "Asia/Shanghai":
        raise SchedulerError("SCHEDULE_TIME_INVALID")


def _validated_counts(values: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in values.items():
        if (
            not key
            or len(key) > 64
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise SchedulerError("JOB_COUNTS_INVALID")
        result[key] = value
    return result
