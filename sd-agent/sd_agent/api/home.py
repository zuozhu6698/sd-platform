from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Request

from sd_agent.adapters.teable import TeableAdapterError
from sd_agent.api.auth import _authenticate
from sd_agent.errors import AppError
from sd_agent.tasks import MyTasksService

router = APIRouter(prefix="/api", tags=["home"])


@router.get("/home/summary")
async def home_summary(
    request: Request,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    _auth, user = await _authenticate(request, sd_token)
    service = getattr(request.app.state.resources, "my_tasks", None)
    if not isinstance(service, MyTasksService):
        raise AppError("TASKS_UNAVAILABLE", "任务服务暂不可用", 503)
    try:
        summary = await service.list_for_person(user.person.person_id, today=date.today())
    except TeableAdapterError as exc:
        status = 503 if exc.retryable else 502
        raise AppError(exc.code, "任务数据暂不可用", status) from exc
    return {
        "data": {
            "counts": {
                "total": summary.total,
                "overdue": summary.overdue,
                "attention": summary.attention,
            },
            "tasks": [
                {
                    "task_id": task.task_id,
                    "kw_id": task.kw_id,
                    "unit_id": task.unit_id,
                    "category": task.category,
                    "content": task.content,
                    "deadline": task.deadline.isoformat() if task.deadline else None,
                    "progress": task.progress,
                    "status": task.status,
                    "revision": task.revision,
                    "ai_flag": task.ai_flag,
                }
                for task in summary.tasks
            ],
        },
        "request_id": request.state.request_id,
    }
