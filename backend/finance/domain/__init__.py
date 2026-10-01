"""finance.domain - PRIVATE.

Nothing outside the finance module may import from here. The single export
surface is `finance.public`, which exposes protocols and read-only schemas
only (docs/ARCHITECTURE-PROPOSAL.md section D).

Sub-layers:
    models/         SQLAlchemy ORM
    value_objects/  Money, Currency, DateRange
    services/       pure logic: dedupe, transfer_match, categorize, budget

Everything here is pure: no I/O, no HTTP, no FastAPI, no request context.
"""

from __future__ import annotations
