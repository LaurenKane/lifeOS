"""core - shared primitives ONLY.

The primitives every module needs and no module owns: money and datetime.

Two hard rules, both architectural conventions rather than automated gates:

1. `core` never imports `finance` (or any other module). It is the bottom of
   the dependency graph.
2. Nothing here holds business logic or talks to the database. If a primitive
   needs a rule from a module, it belongs in that module.

`core` is a SIBLING of `finance` under the `backend/` import root
(ARCHITECTURE.md §3), never its parent.
"""

from __future__ import annotations
