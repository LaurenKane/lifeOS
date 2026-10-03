"""0002 — canonical system-expense account via `system_role`.

The counter-leg of a manual expense used to be identified by the human-editable
label `SYSTEM_EXPENSE_ACCOUNT_NAME`. A renamed account broke posting, and the
import path (`resolve_contra_account`) shared the same fragility. This revision
replaces the name convention with a stable role column.

The role is assigned explicitly, never inferred from a name: naming an account
"Expenses (system)" does not make it the system expense account. Only the role
does.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_finance_system_role"
down_revision: str | None = "0001_finance_ledger"


#: The legacy name that identified the counter-leg account before this revision.
#: Backfill looks at the name only to preserve the behaviour of existing,
#: unambiguous single-row setups.
_LEGACY_NAME: str = "Expenses (system)"

#: The only non-NULL value the CHECK allows today.
_SYSTEM_EXPENSE_ROLE: str = "system_expense"


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute(
        f"""
        ALTER TABLE finance.account
          ADD COLUMN system_role TEXT,
          ADD CONSTRAINT ck_account_system_role
            CHECK (system_role IS NULL OR system_role = '{_SYSTEM_EXPENSE_ROLE}')
        """
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_account_system_role "
        "ON finance.account (system_role) "
        "WHERE system_role IS NOT NULL"
    )

    # Backfill: if exactly one account carries the legacy name, give it the role.
    # More than one means the old resolver would have refused anyway; leave the
    # role unset so the new resolver asks for an explicit designation.
    op.execute(
        f"""
        UPDATE finance.account
        SET system_role = '{_SYSTEM_EXPENSE_ROLE}'
        WHERE id = (
          SELECT id FROM finance.account
          WHERE name = '{_LEGACY_NAME}'
          ORDER BY id
          LIMIT 1
        )
        AND (
          SELECT count(*) FROM finance.account
          WHERE name = '{_LEGACY_NAME}'
        ) = 1
        """
    )


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute("DROP INDEX finance.uq_account_system_role")
    op.execute("ALTER TABLE finance.account DROP COLUMN system_role")
