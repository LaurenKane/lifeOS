"""0003 — the account that pays a card, and a coherent `is_synthesized`.

`docs/adr/0007-imported-card-payment-is-a-transfer.md` decides that a monthly card
payment is a TRANSFER and that the paying account is never guessed. This revision
gives it somewhere to be stored: a self-foreign-key on `finance.account`, so the
answer is an account ID the user registered rather than a name this codebase
pattern-matched on (see migration 0002 for why a name is not an identity).

Two rules live here, both of them about a row that cannot be interpreted.

**`payment_from_account_id`.** NULL means "not registered", which is the honest
state for every account that has never been declared to pay a card, and the
import path refuses rather than picking one. The self-reference carries two
constraints that a plain column would not:

* `ck_account_payment_from_not_self` — an account paying itself is not a
  transfer, it is a mistake, and it is refused by the database rather than by the
  one writer that would have noticed.
* `ON DELETE SET NULL`, not CASCADE and not RESTRICT. Deleting a checking account
  must not delete the card that named it — the card is real and the mapping is
  what was lost, so the card falls back to "unregistered" and the next card
  payment is refused rather than silently re-pointed at a different account.
  CASCADE would delete a live card to clean up a pointer; RESTRICT would make the
  checking account undeletable.

`idx_account_payment_from` is PARTIAL, like every other partial index in this
schema: a NULL mapping carries no lookup value, and only the cards that have one
are ever searched when a paying-side statement row arrives.

**`ck_journal_line_synthesized_reason_present`.** `is_synthesized` and
`synthesized_reason` are two columns carrying one fact, and migration 0001 lets
them disagree: `is_synthesized = TRUE` with a NULL reason is legal today. A
matcher that keys on one column can then be contradicted by the other — a leg
that claims to be synthesized for no stated reason is not a leg anything can
reason about. The CHECK makes the pair total: a synthesized leg always says why,
and a real leg never carries a reason.

Both are raw SQL through `op.execute`. `op.add_column` would emit DDL that the
repository's static invariant checker cannot see, and the migration comments in
0001 and 0002 are written in this style on purpose.
"""

from __future__ import annotations

from alembic import op

revision: str = "0003_payment_from"
down_revision: str | None = "0002_finance_system_role"

# The vocabulary of `synthesized_reason` is migration 0001's CHECK and is not
# restated here: this revision constrains the PAIR of columns, not the list of
# reasons, so adding a reason does not mean editing this file.


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    # The FK target is UNQUALIFIED on purpose: `no_cross_schema_fk` rejects
    # `REFERENCES finance.account.id`, and `SET search_path` above is what makes
    # the bare name resolve into this schema. Same spelling as migration 0001.
    op.execute(
        """
        ALTER TABLE finance.account
          ADD COLUMN payment_from_account_id BIGINT REFERENCES account(id) ON DELETE SET NULL,
          ADD CONSTRAINT ck_account_payment_from_not_self
            CHECK (payment_from_account_id IS NULL OR payment_from_account_id <> id)
        """
    )
    op.execute(
        "CREATE INDEX idx_account_payment_from "
        "ON finance.account (payment_from_account_id) "
        "WHERE payment_from_account_id IS NOT NULL"
    )

    # Every existing row has is_synthesized = FALSE and synthesized_reason = NULL
    # (0001's defaults), so validating this against the table changes nothing for
    # history and closes the pair for everything written after it.
    op.execute(
        """
        ALTER TABLE finance.journal_line
          ADD CONSTRAINT ck_journal_line_synthesized_reason_present
            CHECK (
              (is_synthesized AND synthesized_reason IS NOT NULL)
              OR (NOT is_synthesized AND synthesized_reason IS NULL)
            )
        """
    )


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute("DROP INDEX finance.idx_account_payment_from")
    op.execute(
        "ALTER TABLE finance.journal_line"
        " DROP CONSTRAINT ck_journal_line_synthesized_reason_present"
    )
    op.execute(
        "ALTER TABLE finance.account DROP CONSTRAINT ck_account_payment_from_not_self"
    )
    op.execute("ALTER TABLE finance.account DROP COLUMN payment_from_account_id")
