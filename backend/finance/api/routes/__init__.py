"""finance.api.routes — the FastAPI routers.

One router per domain, all tagged `finance` and mounted by `main` under
`API_V1_PREFIX`. Every route here returns a schema from
`finance.api.schemas`, because that package is the generated OpenAPI contract.

These routes are the only place in the module allowed to touch the database.
That is what keeps `finance.domain` pure and therefore testable.
"""

from __future__ import annotations

from finance.api.routes import (
    accounts,
    analytics,
    categories,
    imports,
    merchants,
    review,
    transactions,
)

#: Every router, in mount order. `main` includes these in sequence.
ROUTERS = (
    accounts.router,
    transactions.router,
    imports.router,
    review.router,
    categories.router,
    merchants.router,
    analytics.router,
)

__all__ = ["ROUTERS"]
