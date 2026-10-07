"""0007 — budgets: one spending limit on one category, one period.

`finance.domain.services.budget` already does the arithmetic (pure integer
minor units, ~17 unit tests) and the frontend budgets page already reads
`GET /api/v1/budgets` — but neither had a table behind it, so the page could
only ever render an error. This revision gives the limit a home.

One row is one budget: a category, a positive limit in minor units, the
currency that limit is denominated in, and how often it repeats. No `name`
column — a budget is a limit ON A CATEGORY, so the category's own name is the
label, and storing it here would be a second copy that goes stale the moment
somebody renames the category. No start/end dates either: the service takes
no period boundaries (it compares `spent` against `limit` for one period the
caller has already selected), and the page's period is the closed set
monthly/quarterly/yearly.

`amount > 0` is a CHECK rather than an application-side guard on purpose: the
database is the authority on what a limit may be, and the router translates
the refusal to 422 (the same rule `routes/categories.py` applies to its
uniqueness constraint — the constraint decides, not a pre-check that could
race it). A zero or negative limit is not a smaller budget; it is a budget
that is already broken.

Raw SQL through `op.execute()`, like migrations 0001-0006: the repository's
static invariant checker reads migration files as SQL text.
"""

from __future__ import annotations

from alembic import op

revision: str = "0007_budget"
down_revision: str | None = "0006_merchant_and_alias_category"


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    # The FK targets are UNQUALIFIED on purpose: the same-schema spelling every
    # earlier migration uses (see 0003/0006). `SET search_path` above is what
    # makes the bare names resolve into this schema — and a schema-qualified
    # target is exactly what the `no_cross_schema_fk` event trigger refuses.
    op.execute(
        """
        CREATE TABLE finance.budget (
          id                          BIGSERIAL PRIMARY KEY,
          category_id                 BIGINT NOT NULL REFERENCES category(id),
          amount                      BIGINT NOT NULL,  -- limit, minor units, positive
          currency                    CHAR(3) NOT NULL REFERENCES currency(code),
          period                      TEXT NOT NULL,
          created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_budget_amount_positive CHECK (amount > 0),
          CONSTRAINT ck_budget_period
            CHECK (period IN ('monthly','quarterly','yearly'))
        )
        """
    )

    # The bootstrap grants schema usage to the app role; the tables themselves
    # are created by the migration role, so read/write is granted explicitly.
    # Same pattern as migration 0004.
    op.execute("GRANT ALL ON finance.budget TO lifeos")
    op.execute("GRANT ALL ON SEQUENCE finance.budget_id_seq TO lifeos")


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute("DROP TABLE IF EXISTS finance.budget")
