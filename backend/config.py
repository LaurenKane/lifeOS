"""pydantic-settings config — environment-driven settings.

Credentials are NOT read here and are never stored in this repository. Enable
Banking's RSA private key lives in the OS keyring (`~/.config/lifeos/`); see
SAFETY.md. The fields below hold non-secret identifiers and connection strings
whose password comes from the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class LifeOSettings(BaseSettings):  # type: ignore[explicit-any]
    """Application settings, read from `LIFEOS_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql://lifeos:lifeos@localhost:5432/lifeos"

    # Application
    APP_NAME: str = "lifeos"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # External identifiers. Non-secret, but recorded in two places on issue
    # (SAFETY.md rule 4): the app_id is the only handle on an Enable Banking app.
    APP_ID: str | None = None
    ENABLE_BANKING_CLIENT_ID: str | None = None

    # API
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        """`CORS_ORIGINS` as a list. Parsed here so no route has to."""
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> LifeOSettings:
    """The process-wide settings.

    Cached: `BaseSettings` reads the environment on construction, and building a
    new instance per request would make settings observable mid-flight.
    """
    return LifeOSettings()
