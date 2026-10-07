"""life.api.routes — the FastAPI routers.

One router per domain, all tagged `life`, all mounted by `main` under
`API_V1_PREFIX` (per the module contract, ADR 0010). Every route returns a
schema from `life.api.schemas` because that is the OpenAPI contract.

These routes are the only place in the module allowed to touch the database;
that is what keeps `life.domain.services` pure — the parser, the cadence
math and the Do-now board run on arguments, not sessions.
"""

from __future__ import annotations

from life.api.routes import (
    actions,
    calendar,
    capture,
    catchup,
    goals,
    pixels,
    reflection,
    thoughts,
    today,
    upkeeps,
    vision,
)

#: Every router, in mount order. `main` includes these in sequence.
ROUTERS = (
    capture.router,
    thoughts.router,
    actions.router,
    goals.router,
    upkeeps.router,
    vision.router,
    pixels.router,
    reflection.router,
    calendar.router,
    today.router,
    catchup.router,
)

__all__ = ["ROUTERS"]
