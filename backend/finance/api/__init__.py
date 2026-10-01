"""finance.api - HTTP surface.

`routes/` holds the FastAPI routers, `schemas/` the Pydantic contract. This
package depends on `finance.ingestion`, `finance.domain` and `core`; nothing
depends on it except `main`.

`schemas` is the *generated* contract the frontend's TypeScript types come from
(`scripts/generate_types.py`). Adding a field here is a schema change, and CI
fails if the OpenAPI spec changed without the TS being regenerated.
"""

from __future__ import annotations
