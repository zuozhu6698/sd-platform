from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from sd_agent.adapters.files import HttpFileScanner, LocalObjectStore
from sd_agent.files import FileServiceError


@respx.mock
async def test_http_scanner_sends_hash_and_validates_response() -> None:
    route = respx.post("https://scanner.test/scan").mock(
        return_value=httpx.Response(
            200,
            json={"clean": True, "engine": "clamav", "signature": None},
        )
    )
    async with httpx.AsyncClient() as http:
        verdict = await HttpFileScanner(base_url="https://scanner.test/", http=http).scan(
            b"content",
            media_type="application/pdf",
        )
    assert verdict.clean is True and verdict.engine == "clamav"
    assert (
        route.calls[0].request.headers["X-Content-SHA256"] == hashlib.sha256(b"content").hexdigest()
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"clean": True, "engine": "", "extra": "bad"}),
    ],
)
@respx.mock
async def test_http_scanner_fails_closed(response: httpx.Response) -> None:
    respx.post("https://scanner.test/scan").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        scanner = HttpFileScanner(base_url="https://scanner.test", http=http)
        with pytest.raises(FileServiceError, match="FILE_SCAN_UNAVAILABLE"):
            await scanner.scan(b"content", media_type="application/pdf")


def test_scanner_rejects_invalid_base_url() -> None:
    with pytest.raises(ValueError, match="base URL"):
        HttpFileScanner(base_url="file:///tmp/scanner", http=object())  # type: ignore[arg-type]


async def test_local_store_writes_reads_and_deletes_atomically(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    stored = await store.put(
        "11111111-1111-4111-8111-111111111111",
        b"safe",
        quarantined=False,
    )
    assert stored.storage_key.startswith("clean/")
    assert await store.get(stored.storage_key) == b"safe"
    await store.delete(stored.storage_key)
    with pytest.raises(FileServiceError, match="FILE_STORAGE_UNAVAILABLE"):
        await store.get(stored.storage_key)


async def test_local_store_separates_quarantine_and_rejects_keys(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    stored = await store.put(
        "11111111-1111-4111-8111-111111111111",
        b"infected",
        quarantined=True,
    )
    assert stored.storage_key.startswith("quarantine/")
    with pytest.raises(FileServiceError, match="FILE_STORAGE_KEY_INVALID"):
        await store.get("../secret")


def test_local_store_requires_absolute_root() -> None:
    with pytest.raises(ValueError, match="absolute"):
        LocalObjectStore(Path("relative"))
