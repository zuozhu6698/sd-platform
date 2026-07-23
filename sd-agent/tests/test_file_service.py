from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest

from sd_agent.files import FileMetadata, FileService, FileServiceError, ScanVerdict, StoredObject

NOW = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)


class FakeScanner:
    def __init__(self, verdict: ScanVerdict | Exception | None = None) -> None:
        self.verdict = verdict or ScanVerdict(True, "test-engine")
        self.media_type: str | None = None

    async def scan(self, _data: bytes, *, media_type: str) -> ScanVerdict:
        self.media_type = media_type
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.quarantined = False

    async def put(self, file_id: str, data: bytes, *, quarantined: bool) -> StoredObject:
        self.quarantined = quarantined
        key = f"{'quarantine' if quarantined else 'clean'}/{file_id}"
        self.objects[key] = data
        return StoredObject(key)

    async def get(self, storage_key: str) -> bytes:
        return self.objects[storage_key]

    async def delete(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[str, FileMetadata] = {}

    async def save(self, metadata: FileMetadata) -> None:
        self.items[metadata.file_id] = metadata

    async def get(self, file_id: str) -> FileMetadata | None:
        return self.items.get(file_id)


def service(
    *,
    scanner: FakeScanner | None = None,
    max_bytes: int = 1024,
) -> tuple[FileService, FakeStore, FakeRepository]:
    store = FakeStore()
    repository = FakeRepository()
    return (
        FileService(
            scanner=scanner or FakeScanner(),
            store=store,
            repository=repository,
            max_bytes=max_bytes,
        ),
        store,
        repository,
    )


def ooxml(*names: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "types")
        for name in names:
            archive.writestr(name, "data")
    return stream.getvalue()


@pytest.mark.parametrize(
    ("media_type", "data"),
    [
        ("application/pdf", b"%PDF-1.7 safe"),
        ("image/png", b"\x89PNG\r\n\x1a\nrest"),
        ("image/jpeg", b"\xff\xd8\xffrest"),
    ],
)
async def test_clean_upload_records_hash_and_controlled_storage(
    media_type: str,
    data: bytes,
) -> None:
    files, store, repository = service()
    result = await files.upload(
        owner_person_id=7,
        task_id=101,
        original_name="  周报.pdf  ",
        declared_media_type=media_type,
        data=data,
        now=NOW,
    )
    assert result.state == "clean" and result.original_name == "周报.pdf"
    assert result.storage_key.startswith("clean/")
    assert len(result.sha256) == 64
    assert repository.items[result.file_id] == result
    assert store.objects[result.storage_key] == data


@pytest.mark.parametrize(
    ("media_type", "data"),
    [
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ooxml("word/document.xml"),
        ),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ooxml("xl/workbook.xml"),
        ),
    ],
)
async def test_valid_ooxml_is_accepted(media_type: str, data: bytes) -> None:
    files, _, _ = service(max_bytes=4096)
    result = await files.upload(
        owner_person_id=7,
        task_id=None,
        original_name="report.docx",
        declared_media_type=media_type,
        data=data,
        now=NOW,
    )
    assert result.media_type == media_type


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("../secret.pdf", "FILE_NAME_INVALID"),
        ("..\\secret.pdf", "FILE_NAME_INVALID"),
        ("", "FILE_NAME_INVALID"),
    ],
)
async def test_unsafe_names_are_rejected(name: str, code: str) -> None:
    files, _, _ = service()
    with pytest.raises(FileServiceError, match=code):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name=name,
            declared_media_type="application/pdf",
            data=b"%PDF-safe",
            now=NOW,
        )


@pytest.mark.parametrize(
    ("media_type", "data", "code"),
    [
        ("text/plain", b"hello", "FILE_TYPE_REJECTED"),
        ("application/pdf", b"not pdf", "FILE_TYPE_MISMATCH"),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ooxml("xl/workbook.xml"),
            "FILE_TYPE_MISMATCH",
        ),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ooxml("word/document.xml", "word/vbaProject.bin"),
            "FILE_MACRO_REJECTED",
        ),
    ],
)
async def test_spoofed_or_dangerous_types_are_rejected(
    media_type: str,
    data: bytes,
    code: str,
) -> None:
    files, _, _ = service(max_bytes=4096)
    with pytest.raises(FileServiceError, match=code):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name="file.docx",
            declared_media_type=media_type,
            data=data,
            now=NOW,
        )


async def test_infected_file_is_quarantined_and_never_downloadable() -> None:
    scanner = FakeScanner(ScanVerdict(False, "clam", "Eicar-Test-Signature"))
    files, store, repository = service(scanner=scanner)
    with pytest.raises(FileServiceError, match="FILE_REJECTED"):
        await files.upload(
            owner_person_id=7,
            task_id=101,
            original_name="bad.pdf",
            declared_media_type="application/pdf",
            data=b"%PDF-infected",
            now=NOW,
        )
    assert store.quarantined is True
    metadata = next(iter(repository.items.values()))
    with pytest.raises(FileServiceError, match="FILE_NOT_FOUND"):
        await files.download(metadata.file_id, person_id=7, roles=frozenset())


async def test_scan_failure_fails_closed_before_storage() -> None:
    files, store, repository = service(scanner=FakeScanner(RuntimeError("offline")))
    with pytest.raises(FileServiceError, match="FILE_SCAN_UNAVAILABLE"):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name="safe.pdf",
            declared_media_type="application/pdf",
            data=b"%PDF-safe",
            now=NOW,
        )
    assert not store.objects and not repository.items


async def test_repository_failure_removes_orphaned_object() -> None:
    files, store, repository = service()

    async def fail(_metadata: FileMetadata) -> None:
        raise RuntimeError("database offline")

    repository.save = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="database offline"):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name="safe.pdf",
            declared_media_type="application/pdf",
            data=b"%PDF-safe",
            now=NOW,
        )
    assert not store.objects


async def test_download_enforces_owner_or_supervision_admin() -> None:
    files, _, _ = service()
    metadata = await files.upload(
        owner_person_id=7,
        task_id=None,
        original_name="safe.pdf",
        declared_media_type="application/pdf",
        data=b"%PDF-safe",
        now=NOW,
    )
    _, content = await files.download(metadata.file_id, person_id=7, roles=frozenset())
    assert content == b"%PDF-safe"
    with pytest.raises(FileServiceError, match="FILE_FORBIDDEN"):
        await files.download(metadata.file_id, person_id=8, roles=frozenset())
    _, admin_content = await files.download(
        metadata.file_id,
        person_id=8,
        roles=frozenset({"supervision_admin"}),
    )
    assert admin_content == content


async def test_size_identity_and_time_validation() -> None:
    files, _, _ = service()
    with pytest.raises(FileServiceError, match="FILE_SIZE_INVALID"):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name="empty.pdf",
            declared_media_type="application/pdf",
            data=b"",
            now=NOW,
        )
    with pytest.raises(FileServiceError, match="FILE_OWNER_INVALID"):
        await files.upload(
            owner_person_id=0,
            task_id=None,
            original_name="safe.pdf",
            declared_media_type="application/pdf",
            data=b"%PDF-safe",
            now=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        await files.upload(
            owner_person_id=7,
            task_id=None,
            original_name="safe.pdf",
            declared_media_type="application/pdf",
            data=b"%PDF-safe",
            now=NOW.replace(tzinfo=None),
        )
