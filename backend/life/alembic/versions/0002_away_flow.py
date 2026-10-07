"""0002 — the away flow: one table, one acknowledgment per day.

The Dashboard's away strip ("you were away N days — do these still matter?")
nags no more than once per day: `life.catchup_ack` records the day the strip
was quieted. The ack is a DATE, not a timestamp, because "seen today" is a
day in the user's timezone (life.local), not an instant — and a plain UNIQUE
on `day` alone is the whole constraint: day is never NULL, so the composite
UNIQUE(other, day) pattern other tables justify is not justified here.

Source of decisions: discovery Q8 (one strip, grounds never shames, ignoring
it must be allowed) and Q22 (anything auto-surfaced is dismissible in one
tap). The strip reads on request — no notification infra, no cron.

Style matches 0001: raw SQL through `op.execute()`, `search_path` set
explicitly, FK targets unqualified (there are none here — the ack points at
a day, not a row).
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_away_flow"
down_revision: str | None = "0001_life_schema"


def upgrade() -> None:
    op.execute("SET search_path TO life, public")
    op.execute(
        """
        CREATE TABLE life.catchup_ack (
          id         BIGSERIAL PRIMARY KEY,
          day        DATE NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_catchup_ack_day UNIQUE (day)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS life.catchup_ack")
