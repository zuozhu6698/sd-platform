from __future__ import annotations

from sd_agent.scheduler.catalog import JOB_SPECS, catalog_hash


def test_catalog_has_the_seven_governed_jobs() -> None:
    assert {spec.name for spec in JOB_SPECS} == {
        "urge_scan",
        "report_reminder",
        "ai_review",
        "weekly_report",
        "monthly_report",
        "reconciliation",
        "weekly_snapshot",
    }
    assert len(JOB_SPECS) == 7
    assert all(spec.timezone == "Asia/Shanghai" for spec in JOB_SPECS)
    assert all(spec.coalesce is True and spec.max_instances == 1 for spec in JOB_SPECS)


def test_catalog_preserves_the_governed_schedule() -> None:
    by_name = {spec.name: spec for spec in JOB_SPECS}
    assert by_name["urge_scan"].cron == {"hour": 9, "minute": 0}
    assert by_name["report_reminder"].cron == {
        "day_of_week": "fri",
        "hour": 12,
        "minute": 0,
    }
    assert by_name["ai_review"].cron == {
        "day_of_week": "fri",
        "hour": 12,
        "minute": 30,
    }
    assert by_name["weekly_report"].cron == {
        "day_of_week": "fri",
        "hour": 14,
        "minute": 0,
    }
    assert by_name["monthly_report"].cron == {"day": "last", "hour": 18, "minute": 0}
    assert by_name["reconciliation"].cron == {"minute": 5}
    assert by_name["weekly_snapshot"].cron == {
        "day_of_week": "fri",
        "hour": 14,
        "minute": 0,
    }


def test_catalog_hash_is_stable_and_sha256_shaped() -> None:
    first = catalog_hash()
    assert first == catalog_hash()
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")
