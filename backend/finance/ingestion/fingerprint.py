"""fingerprint.py — Deterministic content fingerprint for transaction dedup.

FROZEN once shipped (invariant). Hash-pinned by CI via invariants.yaml.

Tier-3 deduplication rule (ARCHITECTURE-PROPOSAL.md §G lines 555-564):
  fingerprint = SHA256(
      lower(trim(collapse_ws(raw_description))) || '|' ||
      raw_amount || '|' || raw_currency || '|' || raw_date || '|' ||
      account_id || '|' || occurrence_index
  )

  occurrence_index = ROW_NUMBER() OVER (PARTITION BY account_id, normalized_description,
      amount, currency, date ORDER BY import_batch_id, raw_line_number)

Key invariants:
  - Pure function: no random, no time, no dict-ordering-dependent iteration
  - stdlib-only: hashlib, re
  - Trailing * # REF:... stripped from description before normalization
  - collapse_ws collapses any whitespace sequence to single space
  - lower(trim(...)) normalizes case and removes leading/trailing whitespace
"""

from __future__ import annotations

import hashlib
import re


def _collapse_ws(text: str) -> str:
    """Collapse any whitespace sequence to a single space."""
    return re.sub(r"\s+", " ", text)


def _strip_trailing_markers(text: str) -> str:
    """Strip trailing * # REF:... patterns from description."""
    # Remove trailing * # REF:... and similar markers
    # e.g. "Starbucks *REF:12345" → "Starbucks"
    # e.g. "Amazon #123" → "Amazon"
    # e.g. "Cafe * #REF" → "Cafe"
    text = re.sub(r"\s+\*+$", "", text)  # trailing *
    text = re.sub(
        r"\s+#+\s*REF\s*:?\s*.*$", "", text, flags=re.IGNORECASE
    )  # trailing # REF:...
    text = re.sub(r"\s+\#+$", "", text)  # trailing ##
    return text


def compute_fingerprint(
    *,
    raw_description: str,
    raw_amount: int,
    raw_currency: str,
    raw_date: str,
    account_id: str,
    occurrence_index: int = 1,
) -> str:
    """Compute a SHA-256 fingerprint for a transaction.

    This is the Tier-3 dedup fingerprint, frozen by hash and pinned by CI.

    Algorithm:
      1. Normalize description: lower(trim(collapse_ws(strip_trailing_*_#_REF:...))))
      2. Join components with '|': normalized_desc | raw_amount | raw_currency | raw_date | account_id | occurrence_index
      3. SHA-256 hash of the joined string

    Args:
        raw_description: Raw transaction description text
        raw_amount: Amount in minor units (signed int)
        raw_currency: ISO 4217 currency code (3 letters)
        raw_date: Date string (YYYY-MM-DD)
        account_id: Account identifier
        occurrence_index: Which occurrence (1-based), from window function

    Returns:
        Hex-encoded SHA-256 digest string (64 characters)
    """
    # Normalize the description
    desc = _collapse_ws(raw_description)
    desc = _strip_trailing_markers(desc)
    desc = desc.lower().strip()

    # Build the fingerprint string: components joined by |
    # Order matters: normalized_desc | raw_amount | raw_currency | raw_date | account_id | occurrence_index
    fingerprint_input = "|".join(
        [
            desc,
            str(raw_amount),
            raw_currency,
            raw_date,
            account_id,
            str(occurrence_index),
        ]
    )

    # SHA-256 hash
    digest = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()

    return digest
