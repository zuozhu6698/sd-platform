from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sd_agent.files import FileServiceError, ScanVerdict, StoredObject

_STORAGE_KEY = re.compile(r"^(clean|quarantine)/[0-9a-f-]{36}$")


class _ScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clean: bool
    engine: str = Field(min_length=1, max_length=80)
    signature: str | None = Field(default=None, max_length=160)


class HttpFileScanner:
    def __init__(self, *, base_url: str, http: httpx.AsyncClient) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("scanner base URL is invalid")
        self._url = f"{base_url.rstrip('/')}/scan"
        self._http = http

    async def scan(self, data: bytes, *, media_type: str) -> ScanVerdict:
        try:
            response = await self._http.post(
                self._url,
                content=data,
                timeout=httpx.Timeout(30, connect=3),
                headers={
                    "Content-Type": media_type,
                    "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
                },
            )
            response.raise_for_status()
            payload = _ScanResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise FileServiceError(
                "FILE_SCAN_UNAVAILABLE",
                "附件扫描服务暂不可用",
                503,
            ) from exc
        return ScanVerdict(payload.clean, payload.engine, payload.signature)


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("file storage root must be absolute")
        self._root = root.resolve()

    async def put(self, file_id: str, data: bytes, *, quarantined: bool) -> StoredObject:
        kind = "quarantine" if quarantined else "clean"
        storage_key = f"{kind}/{file_id}"
        target = self._path(storage_key)
        await asyncio.to_thread(self._write_atomic, target, data)
        return StoredObject(storage_key)

    async def get(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise FileServiceError("FILE_STORAGE_UNAVAILABLE", "附件存储暂不可用", 503) from exc

    async def delete(self, storage_key: str) -> None:
        path = self._path(storage_key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError as exc:
            raise FileServiceError("FILE_STORAGE_UNAVAILABLE", "附件存储暂不可用", 503) from exc

    def _path(self, storage_key: str) -> Path:
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise FileServiceError("FILE_STORAGE_KEY_INVALID", "附件存储键无效", 500)
        path = (self._root / storage_key).resolve()
        if self._root not in path.parents:
            raise FileServiceError("FILE_STORAGE_KEY_INVALID", "附件存储键无效", 500)
        return path

    @staticmethod
    def _write_atomic(target: Path, data: bytes) -> None:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = target.with_suffix(".uploading")
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
