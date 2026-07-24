from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sd_agent.ai.service import LlmRequest


class DeterministicLlmProvider:
    """Gate 3 离线替身：只产出保守结果，不模拟真实模型质量。"""

    @property
    def model(self) -> str:
        return "offline-deterministic-v1"

    async def generate_json(self, request: LlmRequest) -> Mapping[str, Any]:
        data = json.loads(request.data_json)
        if request.purpose == "review":
            return {
                "result": "通过",
                "flags": [],
                "evidence": "离线替身不作风险判断，转人工抽查",
                "confidence": 0.0,
                "question": "",
                "source_ids": [f"T{data['task_id']}", f"L{data['log_id']}"],
            }
        if request.purpose == "weekly_draft":
            task_ids = sorted(
                {
                    int(item["task_id"])
                    for item in data["facts"]
                    if isinstance(item, dict) and isinstance(item.get("task_id"), int)
                }
            )
            if not task_ids:
                return {"content_md": "离线事实表为空", "source_ids": []}
            sources = [f"T{task_id}" for task_id in task_ids]
            lines = ["# 周报事实表（离线替身）"] + [
                f"- 事项事实待人工成文 [{source}]" for source in sources
            ]
            return {"content_md": "\n".join(lines), "source_ids": sources}
        raise ValueError("unsupported LLM purpose")
