from __future__ import annotations

import pytest
from pydantic import ValidationError

from sd_agent.ai import ReviewResult, UrgeText, WeeklyDraft


def test_review_result_accepts_consistent_pass_and_flag() -> None:
    passed = ReviewResult.model_validate(
        {
            "result": "通过",
            "flags": [],
            "evidence": "本期列明完成节点",
            "confidence": 0.9,
            "question": "",
            "source_ids": ["T1", "L2"],
        }
    )
    flagged = ReviewResult.model_validate(
        {
            "result": "标记",
            "flags": ["量化缺失"],
            "evidence": "仅描述推进，未给出完成量",
            "confidence": 0.8,
            "question": "请补充本周完成数量和对应节点",
            "source_ids": ["T1", "L2"],
        }
    )
    assert passed.flags == ()
    assert flagged.flags[0].value == "量化缺失"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "result": "通过",
            "flags": ["量化缺失"],
            "evidence": "事实",
            "confidence": 0.8,
            "question": "",
            "source_ids": ["T1"],
        },
        {
            "result": "标记",
            "flags": [],
            "evidence": "事实",
            "confidence": 0.8,
            "question": "",
            "source_ids": ["T1"],
        },
        {
            "result": "通过",
            "flags": [],
            "evidence": "<script>bad</script>",
            "confidence": 0.8,
            "question": "",
            "source_ids": ["T1"],
        },
        {
            "result": "通过",
            "flags": [],
            "evidence": "事实",
            "confidence": 0.8,
            "question": "",
            "source_ids": ["T1", "T1"],
        },
    ],
)
def test_review_result_rejects_inconsistent_or_unsafe_output(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)


def test_weekly_and_urge_schemas_reject_html_controls_and_bad_sources() -> None:
    weekly = WeeklyDraft(content_md="# 周报\n完成节点 [T1]", source_ids=("T1",))
    urge = UrgeText(text="请及时反馈本期进展")
    assert weekly.source_ids == ("T1",)
    assert urge.text.startswith("请及时")

    for payload in (
        {"content_md": "<b>周报</b>", "source_ids": ["T1"]},
        {"content_md": "周报 [T1]", "source_ids": ["task-1"]},
        {"content_md": "周报 [T1]", "source_ids": ["T1", "T1"]},
    ):
        with pytest.raises(ValidationError):
            WeeklyDraft.model_validate(payload)
    with pytest.raises(ValidationError):
        UrgeText(text="催办\x00内容")
