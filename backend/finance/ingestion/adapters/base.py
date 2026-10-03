"""base - the adapter interface every provider implements.

An adapter's whole job is: bytes or dicts in, `RawRecord`s out. It does not
dedup, does not categorize, does not talk to the database. Everything past this
boundary is provider-agnostic.

`AdapterParseError` carries the line number, because the useful answer to "your
import failed" is which line, and a stack trace through three layers of
pipeline is not it.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from finance.public import RawRecord

__all__ = ["AdapterParseError", "ImportAdapter", "ImportResult", "source_checksum"]


class AdapterParseError(ValueError):
    """A provider row could not be parsed.

    Args:
        message: What was wrong.
        line_number: 1-based position in the source file, when known.
        provider: The provider key, for the error message.
    """

    def __init__(
        self, message: str, line_number: int | None = None, provider: str = ""
    ) -> None:
        self.line_number = line_number
        self.provider = provider
        location = f"{provider or 'provider'}"
        if line_number is not None:
            location = f"{location} line {line_number}"
        super().__init__(f"{location}: {message}")


def source_checksum(payload: bytes) -> str:
    """SHA-256 of the uploaded file.

    Stored on the import batch. Two uploads of the same bytes have the same
    checksum, which is what makes "I already imported this" answerable without
    diffing contents.
    """
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ImportResult:
    """Everything one adapter produced.

    `failed` is a list, not an exception: a single unreadable row in a
    seven-year PDF must not abandon the other 4000. The batch lands `partial`,
    and the failed rows carry their reason.
    """

    provider: str
    import_method: str
    records: list[RawRecord] = field(default_factory=list)
    failed: list[AdapterParseError] = field(default_factory=list)
    source_checksum: str | None = None
    source_filename: str | None = None

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def is_partial(self) -> bool:
        return bool(self.failed)

    @property
    def status(self) -> str:
        """The `import_batch.status` value this result implies."""
        if not self.records:
            return "failed"
        return "partial" if self.failed else "completed"


class ImportAdapter(ABC):
    """Turns one provider's payload into `RawRecord`s.

    Subclasses set the class attributes and implement `parse`, the single
    entry point.
    """

    #: The `import_batch.provider` CHECK value.
    provider: ClassVar[str]
    #: The `import_batch.import_method` CHECK value.
    import_method: ClassVar[str]
    #: Additive confidence weight for a match found through this provider
    #: (section G). PDF extraction is noisier; API data is cleanest.
    confidence_weight: ClassVar[float] = 0.0

    @abstractmethod
    def parse(self, payload: bytes) -> ImportResult:
        """Parse an entire payload."""
