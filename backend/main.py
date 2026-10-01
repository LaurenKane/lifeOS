"""FastAPI app factory — mounts the module routers.

`backend/` is the import root, so `config` and `finance` are top-level
packages. Importing `backend.config` would only work when the repository root
happens to be on `sys.path`, which is not true for the installed wheel.
"""

from __future__ import annotations

from config import get_settings
from fastapi import FastAPI
from finance.api.routes import ROUTERS

settings = get_settings()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        description="Life OS - personal finance ledger with double-entry accounting",
    )

    @app.get("/health", tags=["root"], summary="Health check")
    def health_check() -> dict[str, str]:
        """Liveness. Touches nothing, so it cannot fail because a dependency is down."""
        return {"status": "ok", "service": settings.APP_NAME}

    @app.get("/ready", tags=["root"], summary="Readiness check")
    def readiness_check() -> dict[str, str]:
        """Readiness.

        Reports configuration only. The database probe arrives with M1's
        migrations — a readiness check that queries a table which does not exist
        would report this service as never-ready.
        """
        return {
            "status": "ready",
            "service": settings.APP_NAME,
            "database": "not probed until M1 migrations exist",
        }

    @app.get("/", tags=["root"], summary="Service metadata")
    def root() -> dict[str, str]:
        """Service name, version and where the docs are."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
        }

    for router in ROUTERS:
        app.include_router(router, prefix=settings.API_V1_PREFIX)

    return app


app = create_app()
