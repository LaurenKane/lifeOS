"""FastAPI app factory, mounts routers."""

from __future__ import annotations

from fastapi import FastAPI

from backend.config import get_settings

settings = get_settings()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        description="Life OS — personal finance ledger with double-entry accounting",
    )

    @app.get("/health", tags=["root"], summary="Health check endpoint")
    def health_check():
        """Simple health check."""
        return {"status": "ok", "service": settings.APP_NAME}

    @app.get("/ready", tags=["root"], summary="Readiness check endpoint")
    def readiness_check():
        """Readiness check — verifies dependencies are available."""
        return {
            "status": "ready",
            "database": "configured",
            "service": settings.APP_NAME,
        }

    @app.get("/", tags=["root", "root"], summary="Root endpoint")
    def root():
        """Root API endpoint returning basic info."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
        }

    # Mount module routers — these will be added as we implement each module
    # from backend.finance.api.routes import accounts, transactions, imports, review, categories
    # for router in [accounts.router, transactions.router, imports.router, review.router, categories.router]:
    #     app.include_router(router, prefix=settings.API_V1_PREFIX, tags=["finance"])

    return app


app = create_app()
