from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.ai.service import AiRunRecord
from sd_agent.persistence.models import AiRun


class SqlAiRunRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def record(self, run: AiRunRecord) -> str:
        ai_run_id = str(uuid4())
        async with self._sessions.begin() as session:
            session.add(
                AiRun(
                    ai_run_id=ai_run_id,
                    purpose=run.purpose,
                    input_hash=run.input_hash,
                    source_ids=list(run.source_ids),
                    prompt_version=run.prompt_version,
                    model=run.model,
                    params=run.params,
                    output=run.output,
                    schema_valid=run.schema_valid,
                    reviewed_by=None,
                    review_result=None,
                    created_at=run.created_at,
                )
            )
        return ai_run_id
