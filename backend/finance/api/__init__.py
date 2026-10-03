"""finance.api - HTTP surface.

`routes/` holds the FastAPI routers, `schemas/` the Pydantic contract. This
package depends on `finance.ingestion`, `finance.domain` and `core`; nothing
depends on it except `main`.

`schemas` is the contract the frontend's TypeScript types are *supposed* to come
from. That generation is not implemented and nothing checks for spec drift, so
the frontend API layer is hand-written (ARCHITECTURE.md §7). Adding a field here
is a schema change that no CI job will catch for you.
"""

from __future__ import annotations
