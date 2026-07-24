from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


class ReviewFlag(StrEnum):
    VAGUE = "敷衍填报"
    MISSING_QUANTITY = "量化缺失"
    CONTRADICTION = "前后矛盾"
    GOAL_DRIFT = "目标偏离"
    PROGRESS_RISK = "进度风险"


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: Literal["通过", "标记"]
    flags: tuple[ReviewFlag, ...] = Field(max_length=5)
    evidence: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    question: str = Field(max_length=80)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("evidence", "question")
    @classmethod
    def safe_text(cls, value: str) -> str:
        normalized = value.strip()
        if _CONTROL_RE.search(normalized) or _HTML_RE.search(normalized):
            raise ValueError("AI 文本包含禁止内容")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("来源 ID 无效")
        return value

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.result == "通过" and (self.flags or self.question):
            raise ValueError("通过结论不得包含标记或追问")
        if self.result == "标记" and (not self.flags or not self.question):
            raise ValueError("标记结论必须包含类型和追问")
        return self


class WeeklyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_md: str = Field(min_length=1, max_length=20000)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)

    @field_validator("content_md")
    @classmethod
    def markdown_without_html(cls, value: str) -> str:
        normalized = value.strip()
        if _CONTROL_RE.search(normalized) or _HTML_RE.search(normalized):
            raise ValueError("周报 Markdown 禁止 HTML 或控制字符")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def weekly_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        invalid = any(not re.fullmatch(r"T[1-9]\d*", item) for item in value)
        if len(set(value)) != len(value) or invalid:
            raise ValueError("周报来源 ID 无效")
        return value


class UrgeText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def safe_urge_text(cls, value: str) -> str:
        normalized = value.strip()
        if _CONTROL_RE.search(normalized) or _HTML_RE.search(normalized):
            raise ValueError("催办措辞包含禁止内容")
        return normalized
