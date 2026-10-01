from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Blob:
    """A blob of arbitrary binary data with a MIME type label.

    Used for receipt images, PDF attachments, and similar binary payloads.
    The actual data is expected to live outside the DB (on disk or in object storage),
    and this record stores a reference (path or URL) plus metadata.
    """

    label: str
    data_ref: str  # path or URL
    mime_type: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.label:
            msg = "Blob label must not be empty"
            raise ValueError(msg)

    def __str__(self) -> str:
        return f"Blob(label='{self.label}', mime_type='{self.mime_type}', data_ref='{self.data_ref}')"

    def __repr__(self) -> str:
        return f"Blob(label='{self.label}', mime_type='{self.mime_type}', data_ref='{self.data_ref}')"


def extract_mime_type(
    data: bytes | str, default: str = "application/octet-stream"
) -> str:
    """Guess MIME type from data or return default."""
    if isinstance(data, bytes):
        if data.startswith(b"%PDF"):
            return "application/pdf"
        if data.startswith(b"\x7fELF"):
            return "application/x-executable"
        if data.startswith(b"GIF8"):
            return "image/gif"
        if data[:4] == b"\xff\xd8\xff":
            return "image/jpeg"
    if isinstance(data, str):
        return default
    return default
