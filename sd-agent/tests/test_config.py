from __future__ import annotations

import pytest
from pydantic import ValidationError

from sd_agent.config import Environment, Settings


def production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "ENV": Environment.PRODUCTION,
        "SD_APP_DATABASE_URL": "x" * 24,
        "APP_REDIS_URL": "x" * 24,
        "TEABLE_BASE_URL": "https://teable.example.test",
        "TEABLE_TOKEN": "x" * 24,
        "TEABLE_TABLE_IDS": {
            "org_unit": "tbl_org_unit",
            "person": "tbl_person",
            "role_assignment": "tbl_role_assignment",
            "key_work": "tbl_key_work",
            "task": "tbl_task",
            "task_owner": "tbl_task_owner",
            "progress_log": "tbl_progress_log",
            "urge_log": "tbl_urge_log",
            "work_calendar": "tbl_work_calendar",
        },
        "JWT_SECRET_V1": "x" * 32,
        "CSRF_SECRET": "x" * 32,
        "AUTH_DEV_LOGIN": False,
        "COOKIE_SECURE": True,
        "FILE_SCAN_MODE": "required",
        "FILE_SCAN_BASE_URL": "https://scanner.example.test",
    }
    values.update(overrides)
    return values


def test_production_rejects_missing_secrets() -> None:
    with pytest.raises(ValidationError, match="生产环境缺少高熵配置"):
        Settings(_env_file=None, ENV="production", AUTH_DEV_LOGIN=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("AUTH_DEV_LOGIN", True, "生产环境禁止 AUTH_DEV_LOGIN"),
        ("COOKIE_SECURE", False, "生产环境必须启用 COOKIE_SECURE"),
        ("FILE_SCAN_MODE", "disabled", "生产环境必须配置同步文件扫描服务"),
    ],
)
def test_production_security_invariants(field: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **production_settings(**{field: value}))


def test_redirect_paths_accept_comma_separated_env_shape() -> None:
    settings = Settings(_env_file=None, ALLOWED_REDIRECT_PATHS="/,/home,/m/report")
    assert settings.ALLOWED_REDIRECT_PATHS == ("/", "/home", "/m/report")
