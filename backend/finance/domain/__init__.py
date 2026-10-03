"""finance.domain - PRIVATE.

Nothing outside the finance module may import from here. The single export
surface is `finance.public`, which exposes protocols and read-only schemas
only (ARCHITECTURE.md §3).

Sub-layers:
    models/         SQLAlchemy ORM
    services/       pure logic: transfer_match, categorize, budget

Everything here is pure: no I/O, no HTTP, no FastAPI, no request context.
"""

from __future__ import annotations
