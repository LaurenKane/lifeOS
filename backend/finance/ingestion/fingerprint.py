"""fingerprint.py — the Tier-3 content fingerprint.

**FROZEN once shipped.** Changing anything in this file invalidates every stored
fingerprint and breaks replay, because replay must reproduce the same entry from
the same raw_data. It is guarded by the golden digest in
`backend/finance/tests/unit/test_fingerprint.py`, not by a hash-pin gate — the
deliberate rule is that a change here is a breaking data change and must be
made on purpose.

    fingerprint = SHA256(
        lower(trim(collapse_ws(strip_markers(raw_description)))) || '|' ||
        raw_amount || '|' || raw_currency || '|' || raw_date || '|' ||
        account_id || '|' || occurrence_index
    )

`occurrence_index` is `ROW_NUMBER() OVER (PARTITION BY account_id,
normalized_description, amount, currency, date ORDER BY import_batch_id,
raw_line_number)`. That window function is what makes two genuinely identical
EUR 3.20 coffees on the same day two transactions instead of one transaction
and one phantom duplicate.

Constraints this file must keep, and why:

- **stdlib only** for the algorithm itself (hashlib, re). A third-party
  normaliser would add a version this rule would not capture.
- **no clock, no randomness, no dict/set iteration.** Same input, same digest,
  on any machine, in any order, forever.
- **ONE package import, and it is not optional.** This module imports the
  trailing-marker pattern from `normalize._TRAILING_MARKERS` rather than
  defining its own. It used to define its own copy, and the two drifted apart:
  the copy here also stripped the colon-less `#REF 000123` and `*REF0123456`
  forms that Amex PDFs actually print, which normalize's copy did not. That let
  one purchase normalise differently than it fingerprinted depending on which
  path handled it, so a card statement and a bank statement of the same purchase
  produced different fingerprints and Tier-3 dedup failed SILENTLY — double
  spending, no error. Sharing the pattern makes that unrepresentable.

  So the old rule here — "no imports from the rest of the package" — is
  deliberately relaxed at exactly this one point, and the relaxation is load-
  bearing. `normalize.py` does not import this module, so there is no cycle. The
  remaining isolation requirement is that the *algorithm* stays in this file:
  hashing, the component order and the source-string format are unchanged and
  must not move. `normalize.py` is where the SHARED marker vocabulary lives;
  this file is still where the fingerprint is decided.
"""

from __future__ import annotations

import hashlib
import re

from finance.ingestion.normalize import (
    _TRAILING_MARKERS as _NORMALIZE_TRAILING_MARKERS,
)

# Any whitespace run, including the non-breaking spaces that appear in bank
# exports. Collapsed to one space.
_WHITESPACE = re.compile(r"\s+")

# The trailing-marker strip is NOT defined here. It is imported from
# `normalize._TRAILING_MARKERS`, and that import is deliberate — see the module
# docstring. This module used to carry its own copy of the pattern, and the two
# copies drifted apart: the one here also stripped the colon-less `#REF 000123`
# and `*REF0123456` forms that Amex PDFs actually print, which the copy in
# normalize.py did not. A card statement and a bank statement of one purchase
# could then normalise differently than they fingerprinted, defeating Tier-3
# dedup silently. One pattern, defined once, imported by both.
_TRAILING_MARKERS = _NORMALIZE_TRAILING_MARKERS


def _collapse_whitespace(text: str) -> str:
    """Collapse every run of whitespace to a single space."""
    return _WHITESPACE.sub(" ", text)


def _strip_trailing_markers(text: str) -> str:
    """Remove trailing provider bookkeeping from a description."""
    return _TRAILING_MARKERS.sub("", text)


def normalize_description(raw_description: str) -> str:
    """The description half of the fingerprint: the canonical comparison form.

    `lower(trim(collapse_ws(strip_markers(...))))`, in that order — strip before
    collapsing, so the marker's own trailing whitespace cannot leave a residue
    that survives the trim.
    """
    text = _strip_trailing_markers(raw_description)
    text = _collapse_whitespace(text)
    return text.lower().strip()


def compute_fingerprint(
    *,
    raw_description: str,
    raw_amount: int,
    raw_currency: str,
    raw_date: str,
    account_id: str,
    occurrence_index: int = 1,
) -> str:
    """Compute the Tier-3 fingerprint for one transaction.

    Args:
        raw_description: The provider's description, unmodified.
        raw_amount: Signed minor units. Never a float.
        raw_currency: ISO 4217 code, uppercase.
        raw_date: `YYYY-MM-DD`.
        account_id: Scope key. Cross-account rows never collide.
        occurrence_index: 1-based position among rows identical on every other
            component. Defaults to 1; see the module docstring.

    Returns:
        A 64-character lowercase hex SHA-256 digest.
    """
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
        msg = f"raw_amount must be int minor units, got {type(raw_amount)}"
        raise TypeError(msg)
    if occurrence_index < 1:
        msg = f"occurrence_index is 1-based, got {occurrence_index}"
        raise ValueError(msg)

    canonical = fingerprint_source_string(
        raw_description=raw_description,
        raw_amount=raw_amount,
        raw_currency=raw_currency,
        raw_date=raw_date,
        account_id=account_id,
        occurrence_index=occurrence_index,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_source_string(
    *,
    raw_description: str,
    raw_amount: int,
    raw_currency: str,
    raw_date: str,
    account_id: str,
    occurrence_index: int = 1,
) -> str:
    """The exact string that gets hashed.

    Exposed for tests and for explaining a digest to a human. Not part of the
    algorithm's contract; `compute_fingerprint` is the entry point.
    """
    return "|".join(
        (
            normalize_description(raw_description),
            str(raw_amount),
            raw_currency,
            raw_date,
            account_id,
            str(occurrence_index),
        )
    )
