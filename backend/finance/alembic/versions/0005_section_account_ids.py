"""0005 — remember which account each Revolut section was attributed to.

A Revolut annual statement can carry several products ("Account" and
"Deposit"), and the statement itself never says which local account a product
belongs to. The upload therefore takes a per-section mapping, and without this
column that decision evaporated at COMMIT: a replay re-parsed the file with an
empty mapping, every Deposit row came back `account_id=None`, and the
fingerprint check diverged — loudly, but unrecoverably. Storing the mapping on
the batch is what lets a replay re-attribute each row exactly as the import
did, instead of re-asking a question the user already answered.
"""

from __future__ import annotations

from alembic import op

revision: str = "0005_section_account_ids"
down_revision: str | None = "0004_transfer_review"


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    # Nullable, because every provider but Revolut attributes its rows to a
    # single account: NULL means "no per-section decision was made", which for
    # a single-account statement is the whole truth rather than a gap.
    op.execute("ALTER TABLE finance.import_batch ADD COLUMN section_account_ids JSONB")


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute(
        "ALTER TABLE finance.import_batch DROP COLUMN IF EXISTS section_account_ids"
    )
