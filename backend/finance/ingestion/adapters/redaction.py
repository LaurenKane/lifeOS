"""redaction.py — privacy primitives shared by the PDF adapters.

`finance.source_record.raw_data` is immutable (BEFORE UPDATE/DELETE trigger).
That means anything written into `raw_data` today becomes permanent the moment
import persistence lands — it can only be deleted, and the same trigger blocks
deletion. Redaction therefore has to happen at **parse time**, not at display
time. Masking in the UI would leave the full value in the immutable column.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "mask_iban",
    "redact_card_numbers",
    "redact_ibans",
]

# Compact-format IBAN, used to reduce one found inside free text.
_IBAN_RE: Final = re.compile(
    r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,4}[A-Z0-9]{1,4})\b"
)

# Any printed IBAN: 2 letters + 2 check digits, optional 4-letter bank code, then
# 1-4 numeric groups. Deliberately permissive, and used only for redaction: the
# layout is not uniform across countries (Irish IBANs print four numeric groups,
# Dutch three), so a regex pinned to the Dutch shape would silently pass foreign
# counterparty IBANs through to disk.
_ANY_SPACED_IBAN_RE: Final = re.compile(
    r"\b([A-Z]{2}\d{2}(?:[ ][A-Z]{4})?(?:[ ]\d{2,4}){2,5})\b"
)

# A card number printed inside a description or a continuation line, either as
# an asterisk-masked digit form (`123456******3456`) or as the letter-masked
# forms Amex prints (`XXXX-XXXXXX-23007`, `XXXXXXXXXX23007`). The project's
# privacy rule is last-four only, so every form is reduced to `****` here.
_CARD_NUMBER_RE: Final = re.compile(
    r"\b(?:\d{6,}\*+\d{4}|[Xx]{4}-[Xx]{6}-\d{5}|[Xx]{10}\d{5})\b"
)


def mask_iban(iban: str) -> str:
    """An IBAN reduced to `...` plus its last four characters."""
    return "..." + re.sub(r"\s+", "", iban)[-4:]


def redact_ibans(text: str | None) -> str | None:
    """Mask any IBAN embedded in free text before it reaches a `RawRecord`.

    Applies both a compact-format pass and a permissive spaced-format pass.
    Dutch IBANs print as `NL79 RABO 0000 0000 00`; a compact regex alone misses
    the bank code, and the account survives redaction. A spaced regex alone
    misses compact foreign IBANs. Both passes are required.

    `raw_data` is immutable forever, so this has to happen at parse time rather
    than at display time.
    """
    if text is None:
        return None
    spaced = _ANY_SPACED_IBAN_RE.sub(lambda m: mask_iban(m.group(1)), text)
    return _IBAN_RE.sub(lambda m: mask_iban(m.group(1)), spaced)


def redact_card_numbers(text: str) -> str:
    """Reduce any printed card number to `****`.

    Applied to the description before it reaches a `RawRecord`. The rule this
    enforces is the project's: never persist more than the last four digits of a
    card, and here not even those.
    """
    return _CARD_NUMBER_RE.sub("****", text)
