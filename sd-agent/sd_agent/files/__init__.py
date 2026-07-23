"""Secure file ingestion and object-level access."""

from sd_agent.files.service import (
    FileMetadata,
    FileService,
    FileServiceError,
    ScanVerdict,
    StoredObject,
)

__all__ = [
    "FileMetadata",
    "FileService",
    "FileServiceError",
    "ScanVerdict",
    "StoredObject",
]
