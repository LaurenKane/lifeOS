"""pydantic-settings config — environment-driven settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class LifeOSettings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="LIFEOS_",
        env_file=".env",
        # Do not auto-read .env in production/offline CI
        # .env contains only placeholders, never real secrets
    )

    # Database
    DATABASE_URL: str = "postgresql://lifeos:lifeos@localhost:5432/lifeos"

    # Application
    APP_NAME: str = "lifeos"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Security (placeholders — real values in keyring)
    APP_SECRET: str | None = None
    APP_ID: str | None = None

    # Service providers
    ENABLE_BANKING_CLIENT_ID: str | None = None
    ENABLE_BANKING_CLIENT_SECRET: str | None = None

    # API settings
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8080"


def get_settings() -> LifeOSettings:
    """Get configured settings instance."""
    return LifeOSettings()
