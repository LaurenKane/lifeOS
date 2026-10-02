"""finance.ingestion - the complexity lives here.

Pipeline, in order:
    adapters/   -> RawRecord, one shape regardless of provider
    normalize.py
    identity.py -> IdentityResolver, the 3 dedup tiers
    dedupe.py, transfer_match.py, categorize.py

`fingerprint.py` is FROZEN once shipped: it is SHA-256 hash-pinned by
`invariants.yaml` (`fingerprint_frozen`), because changing it invalidates every
stored fingerprint and breaks replay.

No plugin registry: three adapter classes and an ABC if a fourth appears. The
provider-agnostic part is the schema; only the resolver is provider-aware.
"""

from __future__ import annotations
