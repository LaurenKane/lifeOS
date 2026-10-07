"""0001 — the life module's schema: Goal, WishlistItem, Upkeep, Receipt,
Action, Thought, VisionItem.

Source of truth: `docs/adr/0011-second-module-is-life.md` (naming and the
deliberately-not-a-habit-tracker shape), the discovery-set GLOSSARY.md, and
`docs/adr/0010-adding-a-lifeos-module.md` (the module boundary contract).

Three decisions, all inherited from `finance`'s 0001 so the two schemas read
the same way:

1. Every table lives in the `life` schema; `core` keeps holding only its own
   Alembic bookkeeping table (ADR 0005).

2. Raw SQL through `op.execute()`, never `op.create_table()`. The invariant
   checker scans migration files as text; an op-create-table migration would
   be invisible to it.

3. Foreign key targets are UNQUALIFIED — a qualified target is a
   `no_cross_schema_fk` gate failure. `search_path` is therefore a
   correctness dependency of the DDL: it is SET explicitly below, before any
   table is created.

Inside plpgsql bodies every table name IS schema-qualified: a trigger body
resolves names at COMMIT time in whatever session executes it —
`search_path` being set on the application connection does not make an
unqualified body safe.

The one trigger here enforces THE structural rule the Glossary turns into a
fact: an Action's subactions are exactly one level deep ("pack for the trip"
holds "clothes", "charger"; a subaction can never hold subactions). A CHECK
cannot read another row, so this is a trigger, not a constraint — the same
reasoning the balance trigger carries in ADR 0006, at far smaller stakes.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001_life_schema"
down_revision: str | None = None

#: The eight areas of the Mandala, fixed by discovery Q28. GLOSSARY.md and
#: `life.public.Area` spell the same decision; a change is a migration, not a
#: rename.
_AREAS: str = (
    "('home','hobbies','body','career','money','people','growth','experiences')"
)


def upgrade() -> None:
    # `life` is created by db_bootstrap.sql alongside core and finance (ADR
    # 0010, step 2). The IF NOT EXISTS costs nothing and makes this revision
    # stand alone against a database the bootstrap never touched.
    op.execute("CREATE SCHEMA IF NOT EXISTS life")
    # See module docstring, point 3. `public` stays on the path: pg_trgm lives
    # there and gin_trgm_ops must remain reachable by name.
    op.execute("SET search_path TO life, public")

    _goal_and_wishlist()
    _upkeep()
    _action()
    _thought()
    _receipts()
    _vision()
    _indexes()


def downgrade() -> None:
    # Reverse order of creation: receipt first (it FKs thought and upkeep).
    op.execute("DROP TABLE IF EXISTS life.vision_item")
    op.execute("DROP TABLE IF EXISTS life.thought")
    op.execute("DROP TABLE IF EXISTS life.receipt")
    op.execute("DROP TABLE IF EXISTS life.action")
    op.execute("DROP TABLE IF EXISTS life.upkeep")
    op.execute("DROP TABLE IF EXISTS life.wishlist_item")
    op.execute("DROP TABLE IF EXISTS life.goal")


def _goal_and_wishlist() -> None:
    op.execute(
        f"""
        CREATE TABLE life.goal (
          id            BIGSERIAL PRIMARY KEY,
          title         TEXT NOT NULL,
          why           TEXT,
          area          TEXT,
          -- The Minimum: "pick up the guitar for five minutes." Free TEXT on
          -- purpose — free text is honest, a tier column with only one tier
          -- in use is a stub (discovery Q15).
          minimum       TEXT,
          -- One focus per Goal (glossary: Current thing).
          current_focus TEXT,
          is_active     BOOLEAN NOT NULL DEFAULT TRUE,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_goal_area CHECK (area IS NULL OR area IN {_AREAS})
        )
        """
    )
    op.execute(
        """
        CREATE TABLE life.wishlist_item (
          id         BIGSERIAL PRIMARY KEY,
          goal_id    BIGINT NOT NULL REFERENCES goal(id),
          text       TEXT NOT NULL,
          is_done    BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _upkeep() -> None:
    op.execute(
        """
        CREATE TABLE life.upkeep (
          id         BIGSERIAL PRIMARY KEY,
          title      TEXT NOT NULL UNIQUE,
          aim_days   INTEGER,
          is_active  BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_upkeep_aim_range
            CHECK (aim_days IS NULL OR aim_days BETWEEN 1 AND 365)
        )
        """
    )


def _receipts() -> None:
    op.execute(
        """
        CREATE TABLE life.receipt (
          id           BIGSERIAL PRIMARY KEY,
          upkeep_id    BIGINT NOT NULL REFERENCES upkeep(id),
          -- Capture provenance (discovery Q22): the undo of an
          -- auto-recorded receipt deletes exactly the receipt its capture
          -- created, never a manually filed one. Set only by capture.
          thought_id   BIGINT REFERENCES thought(id),
          -- When the thing was DONE — backdatable ("I actually did this
          -- yesterday"); created_at is when the receipt entered the system.
          receipted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _action() -> None:
    op.execute(
        """
        CREATE TABLE life.action (
          id               BIGSERIAL PRIMARY KEY,
          title            TEXT NOT NULL,
          goal_id          BIGINT REFERENCES goal(id),
          parent_action_id BIGINT REFERENCES action(id),
          due_date         DATE,
          -- Do-now list membership (glossary). Distinct from due_date on
          -- purpose: "due tomorrow" and "queued for tomorrow" are different
          -- promises, and conflating them turns the board into a schedule.
          planned_date     DATE,
          urgent           BOOLEAN NOT NULL DEFAULT FALSE,
          is_done          BOOLEAN NOT NULL DEFAULT FALSE,
          done_at          TIMESTAMPTZ,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_action_done_pair CHECK (is_done = (done_at IS NOT NULL)),
          CONSTRAINT ck_action_sane_due_date
            CHECK (due_date IS NULL OR due_date >= DATE '2020-01-01'),
          CONSTRAINT ck_action_sane_planned_date
            CHECK (planned_date IS NULL OR planned_date >= DATE '2020-01-01')
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION life.fn_action_depth ()
        RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
        IF NEW.parent_action_id IS NOT NULL THEN
          IF NEW.parent_action_id = NEW.id THEN
            RAISE 'life_action_depth: an action cannot parent itself';
          END IF;
          IF EXISTS (
            SELECT 1 FROM life.action parent
            WHERE parent.id = NEW.parent_action_id
              AND parent.parent_action_id IS NOT NULL
          ) THEN
            RAISE 'life_action_depth: a subaction cannot parent another action; subactions are one level deep';
          END IF;
        END IF;
        RETURN NEW;
        END
        $fn$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_action_depth
        BEFORE INSERT OR UPDATE ON life.action
        FOR EACH ROW EXECUTE FUNCTION life.fn_action_depth ()
        """
    )


def _thought() -> None:
    op.execute(
        """
        CREATE TABLE life.thought (
          id            BIGSERIAL PRIMARY KEY,
          text          TEXT NOT NULL,
          resolved_kind TEXT,
          action_id     BIGINT REFERENCES action(id),
          goal_id       BIGINT REFERENCES goal(id),
          upkeep_id     BIGINT REFERENCES upkeep(id),
          resolved_at   TIMESTAMPTZ,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_thought_resolved_kind CHECK (
            resolved_kind IS NULL OR
            resolved_kind IN ('action','goal','upkeep','dismissed','rests')),
          CONSTRAINT ck_thought_resolution_pair CHECK (
            (resolved_kind IS NULL) = (resolved_at IS NULL)),
          CONSTRAINT ck_thought_one_target CHECK (
            (CASE WHEN action_id IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN goal_id   IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN upkeep_id IS NOT NULL THEN 1 ELSE 0 END) <= 1),
          CONSTRAINT ck_thought_kind_matches_target CHECK (
            resolved_kind IS NULL OR resolved_kind IN ('dismissed','rests') OR
            (resolved_kind = 'action'  AND action_id  IS NOT NULL) OR
            (resolved_kind = 'goal'    AND goal_id    IS NOT NULL) OR
            (resolved_kind = 'upkeep'  AND upkeep_id  IS NOT NULL))
        )
        """
    )


def _vision() -> None:
    op.execute(
        f"""
        CREATE TABLE life.vision_item (
          id         BIGSERIAL PRIMARY KEY,
          kind       TEXT NOT NULL,
          text       TEXT,
          media_path TEXT,
          area       TEXT,
          is_active  BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_vision_item_kind CHECK (kind IN ('image','phrase')),
          CONSTRAINT ck_vision_item_area CHECK (area IS NULL OR area IN {_AREAS}),
          CONSTRAINT ck_vision_item_kind_media_pair
            CHECK ((kind = 'image') = (media_path IS NOT NULL)),
          CONSTRAINT ck_vision_item_phrase_has_text
            CHECK (kind <> 'phrase' OR text IS NOT NULL)
        )
        """
    )


def _indexes() -> None:
    # The Inbox is one query shape: unresolved, newest first. Partial exactly
    # on that, so resolved rows (kept forever as provenance) never widen it.
    op.execute(
        """
        CREATE INDEX idx_thought_inbox
        ON life.thought (created_at DESC)
        WHERE resolved_kind IS NULL
        """
    )
    # The paper board queries open top-level actions; partial the same way the
    # finance schema's open-pending index is.
    op.execute(
        """
        CREATE INDEX idx_action_open_top
        ON life.action (planned_date)
        WHERE is_done = FALSE AND parent_action_id IS NULL
        """
    )
    # Receipts are read most (last-done and cadence); the index is the read path.
    op.execute(
        """
        CREATE INDEX idx_receipt_upkeep
        ON life.receipt (upkeep_id, receipted_at DESC)
        """
    )
