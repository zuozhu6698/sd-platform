from __future__ import annotations

from enum import StrEnum

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENV: Environment = Environment.DEVELOPMENT
    APP_NAME: str = "sd-platform"
    APP_PUBLIC_URL: str = "http://127.0.0.1:5173"
    LOG_LEVEL: str = "INFO"
    TZ: str = "Asia/Shanghai"

    SD_APP_DATABASE_URL: SecretStr = SecretStr("")
    APP_REDIS_URL: SecretStr = SecretStr("")
    TEABLE_BASE_URL: str = ""
    TEABLE_TOKEN: SecretStr = SecretStr("")
    TEABLE_TABLE_IDS: dict[str, str] = Field(default_factory=dict)

    JWT_ACTIVE_KID: str = "v1"
    JWT_SECRET_V1: SecretStr = SecretStr("")
    JWT_EXPIRE_MINUTES: int = Field(default=480, ge=5, le=1440)
    AUTH_DEV_LOGIN: bool = True
    SSO_MODE: str = "disabled"
    SSO_STUB_PERSON_ID: int = Field(default=7, gt=0)
    COOKIE_SECURE: bool = False
    CSRF_SECRET: SecretStr = SecretStr("")
    ALLOWED_REDIRECT_PATHS: tuple[str, ...] = (
        "/",
        "/home",
        "/mytasks",
        "/report",
        "/m/report",
    )

    FILE_SCAN_MODE: str = "disabled"
    FILE_SCAN_BASE_URL: str = ""
    FILE_STORAGE_ROOT: str = ""
    FILE_MAX_MB: int = Field(default=20, ge=1, le=100)
    OA_MODE: str = "disabled"
    CRON_ENABLED: bool = False
    OUTBOX_ENABLED: bool = False
    OUTBOX_BATCH_SIZE: int = Field(default=20, ge=1, le=100)
    OUTBOX_POLL_SECONDS: float = Field(default=2.0, gt=0, le=60)
    OUTBOX_MAX_ATTEMPTS: int = Field(default=6, ge=1, le=20)
    OUTBOX_LEASE_SECONDS: int = Field(default=60, ge=5, le=600)

    @field_validator("ALLOWED_REDIRECT_PATHS", mode="before")
    @classmethod
    def parse_redirect_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL 无效")
        return normalized

    @field_validator("OA_MODE", "SSO_MODE")
    @classmethod
    def validate_offline_mode(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.lower()
        allowed = (
            {"disabled", "mock"}
            if info.field_name == "OA_MODE"
            else {
                "disabled",
                "stub",
            }
        )
        if normalized not in allowed:
            raise ValueError("外部依赖模式无效；真实模式等待 EXT-03")
        return normalized

    @model_validator(mode="after")
    def validate_security_contract(self) -> Settings:
        if self.ENV is not Environment.PRODUCTION:
            return self

        required_secrets = {
            "SD_APP_DATABASE_URL": self.SD_APP_DATABASE_URL,
            "APP_REDIS_URL": self.APP_REDIS_URL,
            "TEABLE_TOKEN": self.TEABLE_TOKEN,
            "JWT_SECRET_V1": self.JWT_SECRET_V1,
            "CSRF_SECRET": self.CSRF_SECRET,
        }
        missing = [
            name for name, value in required_secrets.items() if len(value.get_secret_value()) < 24
        ]
        if missing:
            raise ValueError(f"生产环境缺少高熵配置：{', '.join(missing)}")
        if not self.TEABLE_BASE_URL.startswith(("http://", "https://")):
            raise ValueError("生产环境 TEABLE_BASE_URL 无效")
        required_tables = {
            "org_unit",
            "person",
            "role_assignment",
            "key_work",
            "task",
            "task_owner",
            "progress_log",
            "urge_log",
            "work_calendar",
        }
        if set(self.TEABLE_TABLE_IDS) != required_tables or any(
            not table_id.startswith("tbl") for table_id in self.TEABLE_TABLE_IDS.values()
        ):
            raise ValueError("生产环境 TEABLE_TABLE_IDS 必须完整且有效")
        if self.AUTH_DEV_LOGIN:
            raise ValueError("生产环境禁止 AUTH_DEV_LOGIN")
        if self.SSO_MODE == "stub":
            raise ValueError("生产环境禁止 SSO stub")
        if not self.COOKIE_SECURE:
            raise ValueError("生产环境必须启用 COOKIE_SECURE")
        if self.FILE_SCAN_MODE != "required" or not self.FILE_SCAN_BASE_URL:
            raise ValueError("生产环境必须配置同步文件扫描服务")
        if self.OA_MODE == "mock":
            raise ValueError("生产环境禁止 OA mock")
        if not self.FILE_STORAGE_ROOT.startswith("/"):
            raise ValueError("生产环境必须配置绝对文件存储目录")
        return self
