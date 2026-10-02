"""Blob - a reference to binary data that lives outside the database.

Receipt images and PDF statements can be large. Storing them inline makes every
read of the row carry the bytes, so `Blob` holds a `data_ref` (path or URL) plus
metadata, and the bytes stay on disk or in object storage.

`content_hash` is optional but cheap and the reason this type exists in `core`:
an ingestion adapter can compute it at the edge, before the row is written.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Magic-number prefixes we can identify without a full parse. Not exhaustive —
# `extract_mime_type` returns `default` rather than guessing.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),
)

DEFAULT_MIME = "application/octet-stream"


@dataclass(frozen=True, order=True)
class Blob:
    """A pointer to binary data, with enough metadata to find and trust it."""

    label: str
    data_ref: str
    mime_type: str | None = None
    content_hash: str | None = None
    size_bytes: int | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            msg = "Blob label must not be empty"
            raise ValueError(msg)
        if not self.data_ref.strip():
            msg = "Blob data_ref must not be empty"
            raise ValueError(msg)
        if self.size_bytes is not None and self.size_bytes < 0:
            msg = f"Blob size_bytes must be non-negative, got {self.size_bytes}"
            raise ValueError(msg)

    @classmethod
    def from_bytes(cls, label: str, data: bytes) -> Blob:
        """Build a Blob from in-memory bytes, hashing them for an integrity check."""
        return cls(
            label=label,
            data_ref="",
            mime_type=extract_mime_type(data),
            content_hash=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    def __str__(self) -> str:
        return f"Blob({self.label!r}, {self.mime_type or DEFAULT_MIME})"


def extract_mime_type(data: bytes, default: str = DEFAULT_MIME) -> str:
    """Identify a MIME type from the file's magic number.

    Returns `default` rather than guessing when the signature is unknown. A
    wrong MIME type is worse than an absent one, because it gets served.
    """
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    return default


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of `data`. Used for source-file checksums at ingestion."""
    return hashlib.sha256(data).hexdigest()
