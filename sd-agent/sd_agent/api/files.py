from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Cookie, File, Form, Header, Request, UploadFile
from fastapi.responses import Response

from sd_agent.api.auth import _authenticate
from sd_agent.errors import AppError
from sd_agent.files import FileService, FileServiceError

router = APIRouter(prefix="/api/files", tags=["files"])


def _service(request: Request) -> FileService:
    service = getattr(request.app.state.resources, "files", None)
    if not isinstance(service, FileService):
        raise AppError("FILES_UNAVAILABLE", "附件服务暂不可用", 503)
    return service


@router.post("", status_code=201)
async def upload_file(
    request: Request,
    upload: Annotated[UploadFile, File(alias="file")],
    task_id: Annotated[int | None, Form(gt=0)] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    auth, user = await _authenticate(request, sd_token)
    try:
        auth.verify_csrf(user, csrf_token)
    except ValueError as exc:
        raise AppError("CSRF_INVALID", "请求校验失败，请刷新页面后重试", 403) from exc
    limit = request.app.state.settings.FILE_MAX_MB * 1024 * 1024
    content = await upload.read(limit + 1)
    try:
        metadata = await _service(request).upload(
            owner_person_id=user.person.person_id,
            task_id=task_id,
            original_name=upload.filename or "",
            declared_media_type=upload.content_type or "",
            data=content,
            now=datetime.now(UTC),
        )
    except FileServiceError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    finally:
        await upload.close()
    return {
        "data": {
            "file_id": metadata.file_id,
            "name": metadata.original_name,
            "media_type": metadata.media_type,
            "size_bytes": metadata.size_bytes,
            "sha256": metadata.sha256,
            "state": metadata.state,
        },
        "request_id": request.state.request_id,
    }


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    request: Request,
    sd_token: Annotated[str | None, Cookie()] = None,
) -> Response:
    _auth, user = await _authenticate(request, sd_token)
    try:
        metadata, content = await _service(request).download(
            file_id,
            person_id=user.person.person_id,
            roles=frozenset(role.role for role in user.roles),
        )
    except FileServiceError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    return Response(
        content=content,
        media_type=metadata.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata.original_name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-File-SHA256": metadata.sha256,
        },
    )
