"""0004 — transfer review queue and skip list, plus cascade on transfer_match.

A lone candidate the matcher is unsure about (or several candidates at once)
is not a link; it is a question. This revision gives the question somewhere to
live (`finance.transfer_review`) and the answer "never ask this pair again"
somewhere to live (`finance.transfer_skip`).

`transfer_review` holds one row per unmatched outbound leg that needs a human:
the outbound line, the matched candidate ids, and why it was queued
(`multi_candidate` vs `low_confidence`). At most one `pending` row per
outbound (partial unique index); resolving sets `status` and `resolved_at`.

`transfer_skip` holds outbound/inbound pairs the user told the sweep to stop
suggesting (via `ignore_review`). The linker excludes them from candidates.

Also adds `ON DELETE CASCADE` to the two `transfer_match` FKs. Without it a
replay that deletes an entry leaves the match row pointing at a dead line, and
the re-booked entry cannot re-link. Both FKs were inline in migration 0001,
so Postgres auto-named them
`transfer_match_journal_line_id_out_fkey` / `..._in_fkey`.

Raw SQL through `op.execute()`, like migrations 0001-0003: the repository's
static invariant checker reads migration files as SQL text.
"""

from __future__ import annotations

from alembic import op

revision: str = "0004_transfer_review"
down_revision: str | None = "0003_payment_from"


def upgrade() -> None:
    op.execute("SET search_path TO finance, public")

    op.execute(
        """
        CREATE TABLE finance.transfer_review (
          id                          BIGSERIAL PRIMARY KEY,
          outbound_journal_line_id    BIGINT NOT NULL
            REFERENCES journal_line(id) ON DELETE CASCADE,
          candidate_journal_line_ids  BIGINT[] NOT NULL,
          reason                      TEXT NOT NULL
            CHECK (reason IN ('multi_candidate','low_confidence')),
          status                      TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','confirmed','rejected','ignored')),
          created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
          resolved_at                 TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_transfer_review_pending_outbound
          ON finance.transfer_review (outbound_journal_line_id)
          WHERE status = 'pending'
        """
    )

    op.execute(
        """
        CREATE TABLE finance.transfer_skip (
          id                          BIGSERIAL PRIMARY KEY,
          outbound_journal_line_id    BIGINT NOT NULL
            REFERENCES journal_line(id) ON DELETE CASCADE,
          inbound_journal_line_id     BIGINT NOT NULL
            REFERENCES journal_line(id) ON DELETE CASCADE,
          created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_transfer_skip_pair
            UNIQUE (outbound_journal_line_id, inbound_journal_line_id)
        )
        """
    )

    # A replay deletes entries; without CASCADE the match row outlives its
    # lines and the re-booked lines can never re-link (dormant M6 replay
    # FK violation). The names are Postgres' auto-names for the inline FKs
    # in migration 0001.
    op.execute(
        "ALTER TABLE finance.transfer_match"
        " DROP CONSTRAINT IF EXISTS transfer_match_journal_line_id_out_fkey"
    )
    op.execute(
        """
        ALTER TABLE finance.transfer_match
          ADD CONSTRAINT transfer_match_journal_line_id_out_fkey
          FOREIGN KEY (journal_line_id_out)
          REFERENCES journal_line(id) ON DELETE CASCADE
        """
    )
    op.execute(
        "ALTER TABLE finance.transfer_match"
        " DROP CONSTRAINT IF EXISTS transfer_match_journal_line_id_in_fkey"
    )
    op.execute(
        """
        ALTER TABLE finance.transfer_match
          ADD CONSTRAINT transfer_match_journal_line_id_in_fkey
          FOREIGN KEY (journal_line_id_in)
          REFERENCES journal_line(id) ON DELETE CASCADE
        """
    )

    # The bootstrap grants schema usage to the app role; the tables themselves
    # are created by the migration role, so read/write is granted explicitly.
    op.execute("GRANT ALL ON finance.transfer_review TO lifeos")
    op.execute("GRANT ALL ON finance.transfer_skip TO lifeos")
    op.execute(
        "GRANT ALL ON SEQUENCE"
        " finance.transfer_review_id_seq,"
        " finance.transfer_skip_id_seq TO lifeos"
    )


def downgrade() -> None:
    op.execute("SET search_path TO finance, public")

    # Restore the pre-CASCADE FKs: inline references with no ON DELETE action.
    op.execute(
        "ALTER TABLE finance.transfer_match"
        " DROP CONSTRAINT IF EXISTS transfer_match_journal_line_id_out_fkey"
    )
    op.execute(
        """
        ALTER TABLE finance.transfer_match
          ADD CONSTRAINT transfer_match_journal_line_id_out_fkey
          FOREIGN KEY (journal_line_id_out)
          REFERENCES journal_line(id)
        """
    )
    op.execute(
        "ALTER TABLE finance.transfer_match"
        " DROP CONSTRAINT IF EXISTS transfer_match_journal_line_id_in_fkey"
    )
    op.execute(
        """
        ALTER TABLE finance.transfer_match
          ADD CONSTRAINT transfer_match_journal_line_id_in_fkey
          FOREIGN KEY (journal_line_id_in)
          REFERENCES journal_line(id)
        """
    )

    op.execute("DROP TABLE IF EXISTS finance.transfer_skip")
    op.execute("DROP TABLE IF EXISTS finance.transfer_review")
