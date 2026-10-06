"""0006 — let merchants and aliases carry a category.

The engine resolves BOTH aliases (layer 2) and known merchants (layers 3-4)
to a CATEGORY, and the tables could not carry one: `merchant` had no category
at all and `merchant_alias` pointed only at a canonical merchant, never at a
category. Without these columns neither projection in `finance.ingestion.rules`
has anything to load, so layers 2-4 stay empty no matter what the user files.

Both columns are nullable: a merchant with no category is a name the system
knows but has not filed yet, and it feeds nothing — which is the honest
answer for a name nobody categorised. The alias's `merchant_id` turns
nullable with them: an alias is a raw string -> category mapping, and the
canonical merchant is now an optional annotation rather than the target.

Nothing in production wrote these tables before this revision (no writer
inserts into either), so adding nullable columns and relaxing one NOT NULL
rewrites no history and needs no backfill. No data is seeded here: merchant
data is the owner's to file, not this migration's to invent.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006_merchant_and_alias_category"
down_revision: str | None = "0005_section_account_ids"


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    # The FK targets are UNQUALIFIED on purpose: the same-schema spelling every
    # earlier migration uses (see 0003). `SET search_path` above is what makes
    # the bare name resolve into this schema.
    op.execute(
        "ALTER TABLE finance.merchant ADD COLUMN category_id BIGINT REFERENCES category(id)"
    )
    op.execute(
        "ALTER TABLE finance.merchant_alias ADD COLUMN category_id BIGINT REFERENCES category(id)"
    )
    op.execute(
        "ALTER TABLE finance.merchant_alias ALTER COLUMN merchant_id DROP NOT NULL"
    )


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute(
        "ALTER TABLE finance.merchant_alias ALTER COLUMN merchant_id SET NOT NULL"
    )
    op.execute("ALTER TABLE finance.merchant_alias DROP COLUMN category_id")
    op.execute("ALTER TABLE finance.merchant DROP COLUMN category_id")
