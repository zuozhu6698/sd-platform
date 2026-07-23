from __future__ import annotations

import hashlib
import io
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Protocol
from uuid import uuid4

ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    clean: bool
    engine: str
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str


@dataclass(frozen=True, slots=True)
class FileMetadata:
    file_id: str
    owner_person_id: int
    task_id: int | None
    storage_key: str
    original_name: str
    media_type: str
    size_bytes: int
    sha256: str
    state: str
    scan_result: dict[str, str | bool | None]
    created_at: datetime
    scanned_at: datetime


class FileServiceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


class FileScanner(Protocol):
    async def scan(self, data: bytes, *, media_type: str) -> ScanVerdict: ...


class ObjectStore(Protocol):
    async def put(self, file_id: str, data: bytes, *, quarantined: bool) -> StoredObject: ...

    async def get(self, storage_key: str) -> bytes: ...


class FileRepository(Protocol):
    async def save(self, metadata: FileMetadata) -> None: ...

    async def get(self, file_id: str) -> FileMetadata | None: ...


class FileService:
    def __init__(
        self,
        *,
        scanner: FileScanner,
        store: ObjectStore,
        repository: FileRepository,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        if max_bytes < 1024:
            raise ValueError("max_bytes is too small")
        self._scanner = scanner
        self._store = store
        self._repository = repository
        self._max_bytes = max_bytes

    async def upload(
        self,
        *,
        owner_person_id: int,
        task_id: int | None,
        original_name: str,
        declared_media_type: str,
        data: bytes,
        now: datetime,
    ) -> FileMetadata:
        current_time = _require_utc(now)
        if owner_person_id <= 0 or (task_id is not None and task_id <= 0):
            raise FileServiceError("FILE_OWNER_INVALID", "附件归属无效", 422)
        name = _safe_name(original_name)
        if not data or len(data) > self._max_bytes:
            raise FileServiceError("FILE_SIZE_INVALID", "附件为空或超过大小限制", 413)
        media_type = _detect_media_type(data, declared_media_type)
        try:
            verdict = await self._scanner.scan(data, media_type=media_type)
        except FileServiceError:
            raise
        except Exception as exc:
            raise FileServiceError("FILE_SCAN_UNAVAILABLE", "附件扫描服务暂不可用", 503) from exc

        file_id = str(uuid4())
        stored = await self._store.put(file_id, data, quarantined=not verdict.clean)
        state = "clean" if verdict.clean else "quarantined"
        metadata = FileMetadata(
            file_id=file_id,
            owner_person_id=owner_person_id,
            task_id=task_id,
            storage_key=stored.storage_key,
            original_name=name,
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            state=state,
            scan_result={
                "clean": verdict.clean,
                "engine": verdict.engine[:80],
                "signature": verdict.signature[:160] if verdict.signature else None,
            },
            created_at=current_time,
            scanned_at=current_time,
        )
        await self._repository.save(metadata)
        if not verdict.clean:
            raise FileServiceError("FILE_REJECTED", "附件未通过安全扫描", 422)
        return metadata

    async def download(
        self,
        file_id: str,
        *,
        person_id: int,
        roles: frozenset[str],
    ) -> tuple[FileMetadata, bytes]:
        if not file_id or person_id <= 0:
            raise FileServiceError("FILE_NOT_FOUND", "附件不存在", 404)
        metadata = await self._repository.get(file_id)
        if metadata is None or metadata.state != "clean":
            raise FileServiceError("FILE_NOT_FOUND", "附件不存在", 404)
        if metadata.owner_person_id != person_id and "supervision_admin" not in roles:
            raise FileServiceError("FILE_FORBIDDEN", "无权访问该附件", 403)
        return metadata, await self._store.get(metadata.storage_key)


def _safe_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if (
        not normalized
        or len(normalized) > 255
        or "/" in normalized
        or "\\" in normalized
        or PurePath(normalized).name != normalized
        or any(ord(character) < 32 for character in normalized)
        or normalized in {".", ".."}
    ):
        raise FileServiceError("FILE_NAME_INVALID", "附件名称无效", 422)
    return normalized


def _detect_media_type(data: bytes, declared: str) -> str:
    if declared not in ALLOWED_MEDIA_TYPES:
        raise FileServiceError("FILE_TYPE_REJECTED", "不支持该附件类型", 415)
    if declared == "application/pdf" and data.startswith(b"%PDF-"):
        return declared
    if declared == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return declared
    if declared == "image/jpeg" and data.startswith(b"\xff\xd8\xff"):
        return declared
    if declared.startswith("application/vnd.openxmlformats-officedocument"):
        return _detect_ooxml(data, declared)
    raise FileServiceError("FILE_TYPE_MISMATCH", "附件内容与声明类型不一致", 415)


def _detect_ooxml(data: bytes, declared: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (ValueError, zipfile.BadZipFile) as exc:
        raise FileServiceError("FILE_TYPE_MISMATCH", "附件内容与声明类型不一致", 415) from exc
    if "[Content_Types].xml" not in names or any(
        name.startswith("/") or ".." in PurePath(name).parts for name in names
    ):
        raise FileServiceError("FILE_TYPE_MISMATCH", "附件结构无效", 415)
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise FileServiceError("FILE_MACRO_REJECTED", "禁止上传含宏附件", 415)
    expected_prefix = "word/" if declared.endswith("wordprocessingml.document") else "xl/"
    if not any(name.startswith(expected_prefix) for name in names):
        raise FileServiceError("FILE_TYPE_MISMATCH", "附件内容与声明类型不一致", 415)
    return declared


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)
