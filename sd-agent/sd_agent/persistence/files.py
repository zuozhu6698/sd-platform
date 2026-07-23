from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from sd_agent.files import FileMetadata
from sd_agent.persistence.models import FileObject


class SqlFileRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def save(self, metadata: FileMetadata) -> None:
        async with self._sessions.begin() as session:
            session.add(
                FileObject(
                    file_id=metadata.file_id,
                    owner_person_id=metadata.owner_person_id,
                    task_id=metadata.task_id,
                    storage_key=metadata.storage_key,
                    original_name=metadata.original_name,
                    media_type=metadata.media_type,
                    size_bytes=metadata.size_bytes,
                    sha256=metadata.sha256,
                    state=metadata.state,
                    scan_result=metadata.scan_result,
                    created_at=metadata.created_at,
                    scanned_at=metadata.scanned_at,
                    bound_at=None,
                )
            )

    async def get(self, file_id: str) -> FileMetadata | None:
        async with self._sessions() as session:
            row = await session.get(FileObject, file_id)
        if row is None:
            return None
        return FileMetadata(
            file_id=row.file_id,
            owner_person_id=row.owner_person_id,
            task_id=row.task_id,
            storage_key=row.storage_key,
            original_name=row.original_name,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            state=row.state,
            scan_result=row.scan_result or {},
            created_at=row.created_at,
            scanned_at=row.scanned_at or row.created_at,
        )
