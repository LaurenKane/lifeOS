"""finance - the finance module.

Layering, arrows pointing inward (docs/ARCHITECTURE-PROPOSAL.md section D):

    api  ->  ingestion  ->  domain  ->  core

`finance.domain` is PRIVATE: `finance.public` is the only export surface.
`finance.public` exports protocols and read-only schemas, never logic.
"""

from __future__ import annotations
