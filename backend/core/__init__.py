"""core - shared primitives ONLY.

The primitives every module needs and no module owns: money, datetime, blob,
preference, recurrence.

Two hard rules, both enforced by `.importlinter`:

1. `core` never imports `finance` (or any other module). It is the bottom of
   the dependency graph.
2. Nothing here holds business logic or talks to the database. If a primitive
   needs a rule from a module, it belongs in that module.

`core` is a SIBLING of `finance` under the `backend/` import root
(docs/ARCHITECTURE-PROPOSAL.md section D), never its parent.
"""

from __future__ import annotations
