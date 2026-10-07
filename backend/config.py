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

    # The life module (second module). "Today" and "last done N days ago" are
    # the user's local day, not the server's UTC clock: Europe/Amsterdam is
    # where the operator lives; zoneinfo resolves it, no DST bookkeeping.
    LOCAL_TIMEZONE: str = "Europe/Amsterdam"
    # Vision-board media files ("links to local media, not copies in the
    # database"). A mounted volume in compose; never inside the repo.
    MEDIA_DIR: str = "/data/media"

    # ntfy push gateway (the daily digest). Token is ENV-ONLY: it arrives
    # through the deployer's own environment or an UNTRACKED override file —
    # never a tracked file of this repository (SAFETY.md rule 3).
    NTFY_SERVER_URL: str = ""
    NTFY_TOPIC: str = ""
    NTFY_TOKEN: str = ""

    # The calendar feed's URL token (ADR 0013). Same house rules as the ntfy
    # token: ENV-ONLY, never a tracked file of this repository (SAFETY.md
    # rule 3). Rotating = changing the value (calendar clients re-subscribe
    # by URL); unset = the feed answers 404 — off, not locked-and-waiting.
    ICS_TOKEN: str = ""

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


def pg_connect_args(*schemas: str) -> dict[str, str]:
    """`connect_args` pinning a psycopg session's `search_path`.

    Passed to every `create_engine(..., connect_args=...)` in this project:
    the application engine, and both Alembic `env.py` files. All three MUST
    pass it, or the failure mode is the bad one - a migration that resolves a
    name against a different search_path than the running application, so a
    trigger body or an index DDL that works during `alembic upgrade` becomes
    `relation does not exist` in production.

    `public` is always last on the path, never absent: `pg_trgm` lives there
    (see `backend/db_bootstrap.sql`), so `gin_trgm_ops` must stay reachable by
    name even while `finance` comes first.

    The DB-level alternatives - `ALTER ROLE ... SET search_path` or `ALTER
    DATABASE ... SET search_path` - were rejected for one concrete reason:
    neither can be applied to a database that does not exist yet, and the test
    database is created at fixture time. A connection-level option works against
    a database that is created by the fixture and migrated a second later.
    """
    return {"options": "-csearch_path=" + ",".join((*schemas, "public"))}
