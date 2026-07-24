from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    cron: Mapping[str, int | str]
    timezone: str = "Asia/Shanghai"
    coalesce: bool = True
    max_instances: int = 1
    misfire_grace_seconds: int = 900


def _cron(**values: int | str) -> Mapping[str, int | str]:
    return MappingProxyType(values)


JOB_SPECS: tuple[JobSpec, ...] = (
    JobSpec("urge_scan", _cron(hour=9, minute=0)),
    JobSpec("report_reminder", _cron(day_of_week="fri", hour=12, minute=0)),
    JobSpec("ai_review", _cron(day_of_week="fri", hour=12, minute=30)),
    JobSpec("weekly_report", _cron(day_of_week="fri", hour=14, minute=0)),
    # 原设计只规定“月末”；18:00 是可配置基线，正式启用前须由业务负责人确认。
    JobSpec("monthly_report", _cron(day="last", hour=18, minute=0)),
    JobSpec("reconciliation", _cron(minute=5)),
    JobSpec("weekly_snapshot", _cron(day_of_week="fri", hour=14, minute=0)),
)


def catalog_hash(specs: tuple[JobSpec, ...] = JOB_SPECS) -> str:
    payload = [
        {
            "name": spec.name,
            "cron": dict(sorted(spec.cron.items())),
            "timezone": spec.timezone,
            "coalesce": spec.coalesce,
            "max_instances": spec.max_instances,
            "misfire_grace_seconds": spec.misfire_grace_seconds,
        }
        for spec in specs
    ]
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("ascii")).hexdigest()
