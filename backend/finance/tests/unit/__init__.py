"""Unit tests: pure functions, no I/O, no database.

The fingerprint, money, dedup, transfer-match and categorization rules all live
here because none of them need a Postgres instance to be exercised. That is a
design goal, not a convenience — it is what makes the dedup logic testable at
all.
"""
