from __future__ import annotations

from enum import StrEnum

from pydantic import Field, SecretStr, field_validator, model_validator
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

    JWT_ACTIVE_KID: str = "v1"
    JWT_SECRET_V1: SecretStr = SecretStr("")
    JWT_EXPIRE_MINUTES: int = Field(default=480, ge=5, le=1440)
    AUTH_DEV_LOGIN: bool = True
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
    CRON_ENABLED: bool = False

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
        if self.AUTH_DEV_LOGIN:
            raise ValueError("生产环境禁止 AUTH_DEV_LOGIN")
        if not self.COOKIE_SECURE:
            raise ValueError("生产环境必须启用 COOKIE_SECURE")
        if self.FILE_SCAN_MODE != "required" or not self.FILE_SCAN_BASE_URL:
            raise ValueError("生产环境必须配置同步文件扫描服务")
        return self
