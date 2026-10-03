"""finance - the finance module.

Layering, arrows pointing inward (ARCHITECTURE.md §3):

    api  ->  ingestion  ->  domain  ->  core

`finance.domain` is PRIVATE: `finance.public` is the only export surface.
`finance.public` exports protocols and read-only schemas, never logic.
"""

from __future__ import annotations
