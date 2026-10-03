"""0001 — the double-entry ledger schema, in full.

Source of truth: docs/ARCHITECTURE-PROPOSAL.md section E, corrected against a
live PostgreSQL 17.11 instance. The corrections are load-bearing and each one is
commented at the site it applies to, so the next reader does not "restore" the
proposal's version by mistake.

Three decisions shape everything below.

1. Every table lives in the `finance` schema. `core` holds only its own
   Alembic bookkeeping table. See docs/adr/0005-schema-ownership.md.

2. Raw SQL through `op.execute()`, never `op.create_table()`. The
   `raw_data_immutable` invariant scans migration files with regexes written
   against SQL text, so emitting SQL keeps the static check and the database in
   the same language. An `op.create_table()` migration would be invisible to the
   checker that is supposed to police it.

3. Foreign key targets are UNQUALIFIED. `no_cross_schema_fk` matches
   `REFERENCES <schema>.<table>` and rejects it, so a qualified target is a
   gate failure. search_path is therefore a correctness dependency of the DDL,
   not a convenience, and it is set explicitly below.

Inside plpgsql bodies every table name is schema-qualified anyway. A trigger
body resolves names against the search_path of whatever session happens to be
executing it, at COMMIT time, so an unqualified name that happens to work during
`alembic upgrade` becomes `relation does not exist` inside the running
application. That is the worst possible moment to find out, so it is not left to
a session setting.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001_finance_ledger"
down_revision: str | None = None


def upgrade() -> None:
    # pg_trgm backs idx_sr_desc_trgm and idx_alias_raw. The compose bootstrap
    # installs it too; doing it here as well means this revision stands alone
    # against a database the bootstrap never touched.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # See module docstring, point 3. `public` stays on the path because the
    # bootstrap installs pg_trgm there, and gin_trgm_ops must remain reachable.
    op.execute("SET search_path TO finance, public")

    _reference_tables()
    _accounts()
    _import_pipeline()
    _merchant_and_category()
    _ledger()
    _raw_side()
    _recurring()
    _triggers()


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def _reference_tables() -> None:
    op.execute(
        """
        CREATE TABLE finance.currency (
          code        CHAR(3) PRIMARY KEY,
          name        TEXT NOT NULL,
          -- Authority for interpreting BIGINT minor units. 0..18 because that
          -- is the range a NUMERIC(18,x) column can carry, and a currency
          -- outside it cannot round-trip through amount_base.
          decimals    SMALLINT NOT NULL DEFAULT 2,
          is_active   BOOLEAN NOT NULL DEFAULT TRUE,
          CONSTRAINT ck_currency_decimals CHECK (decimals BETWEEN 0 AND 18)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.exchange_rate (
          id              BIGSERIAL PRIMARY KEY,
          date            DATE NOT NULL,
          base_currency   CHAR(3) NOT NULL REFERENCES currency(code),  -- always 'EUR'
          quote_currency  CHAR(3) NOT NULL REFERENCES currency(code),
          rate            NUMERIC(20,10) NOT NULL,
          source          TEXT NOT NULL DEFAULT 'ecb',
          CONSTRAINT uq_exchange_rate UNIQUE (date, base_currency, quote_currency),
          -- A zero or negative rate converts a signed amount into a nonsense
          -- one, and the arithmetic happens far from the insert that caused it.
          CONSTRAINT ck_exchange_rate_positive CHECK (rate > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.institution (
          id            BIGSERIAL PRIMARY KEY,
          name          TEXT NOT NULL UNIQUE,
          provider_key  TEXT NOT NULL UNIQUE,   -- 'rabobank' | 'revolut' | 'amex_nl'
          country_code  CHAR(2) NOT NULL DEFAULT 'NL'
        )
        """
    )


# ---------------------------------------------------------------------------
# Accounts — asset vs liability first-class
# ---------------------------------------------------------------------------


def _accounts() -> None:
    op.execute(
        """
        CREATE TABLE finance.account (
          id                 BIGSERIAL PRIMARY KEY,
          institution_id     BIGINT REFERENCES institution(id),
          name               TEXT NOT NULL,
          account_type       TEXT NOT NULL CHECK (account_type IN
                               ('checking','savings','credit_card','cash','investment','loan','mortgage')),
          account_nature     TEXT NOT NULL CHECK (account_nature IN ('asset','liability','equity')),
          currency           CHAR(3) NOT NULL REFERENCES currency(code),
          iban               TEXT,
          masked_pan         TEXT,
          is_active          BOOLEAN NOT NULL DEFAULT TRUE,
          is_hidden          BOOLEAN NOT NULL DEFAULT FALSE,
          sort_order         INT NOT NULL DEFAULT 0,
          -- Investment seam. No FK until the table exists, and no FK to a table
          -- in another schema is possible anyway (no_cross_schema_fk).
          security_id        BIGINT
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_account_nature "
        "ON finance.account (account_nature) WHERE is_active"
    )
    op.execute(
        """
        CREATE TABLE finance.provider_account_link (
          id                    BIGSERIAL PRIMARY KEY,
          institution_id        BIGINT NOT NULL REFERENCES institution(id),
          account_id            BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
          provider_account_id   TEXT NOT NULL,   -- IBAN / masked PAN, as the provider sends it
          provider_account_name TEXT,
          CONSTRAINT uq_pal_institution_provider
            UNIQUE (institution_id, provider_account_id)
        )
        """
    )


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------


def _import_pipeline() -> None:
    op.execute(
        """
        CREATE TABLE finance.import_batch (
          id                BIGSERIAL PRIMARY KEY,
          institution_id    BIGINT REFERENCES institution(id),
          -- NULLABLE AND NOT THE ROW'S ACCOUNT. source_record.account_id is
          -- authoritative, per row. One file can cover two accounts: a Revolut
          -- statement carries Current (340 rows) + Deposit (11 rows) and lists
          -- two own IBANs without saying which is which (doc 11 section 3.6).
          -- One file may therefore produce several batches, same
          -- source_checksum, one account each -- preferred, since import_batch is
          -- a unit of work. Rows whose ownership is ambiguous are HELD for
          -- explicit user confirmation and are NOT written under a guessed
          -- account_id: docs/adr/0003-import-decisions-real-export.md Decision 4.
          account_id        BIGINT REFERENCES account(id),
          -- v1 list: docs/adr/0002-import-provider-enum.md. amex_csv RETIRED,
          -- revolut_csv DEFERRED (its stable `id` is unverified, doc 11 section 7),
          -- google_wallet EXCLUDED (a dedup liability, R02 section 4).
          -- rabobank_pdf / revolut_pdf are declared but have no FileAdapter yet:
          -- listed, not uploadable.
          provider          TEXT NOT NULL CHECK (provider IN
                              ('enable_banking','amex_pdf','rabobank_pdf','revolut_pdf','manual')),
          import_method     TEXT NOT NULL CHECK (import_method IN ('api','csv','pdf','manual')),
          status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                              ('pending','processing','completed','failed','partial')),
          source_filename   TEXT,
          source_checksum   CHAR(64),
          raw_payload       JSONB,               -- gzipped if large; NEVER removed
          stats             JSONB NOT NULL DEFAULT '{}',
          started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at      TIMESTAMPTZ
        )
        """
    )


# ---------------------------------------------------------------------------
# Raw side — the import pipeline's own record of what the bank said
# ---------------------------------------------------------------------------


def _raw_side() -> None:
    # Created after the ledger, not before it, because it points at journal_entry.
    op.execute(
        """
        CREATE TABLE finance.source_record (
          id                 BIGSERIAL PRIMARY KEY,
          import_batch_id    BIGINT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
          account_id         BIGINT NOT NULL REFERENCES account(id),
          -- Enable Banking entry_reference; NULL on every v1 PDF path (Amex IDs
          -- change; Rabobank End-to-End ID is unusable, doc 11 section 3.4).
          provider_txn_id    TEXT,
          fingerprint        BYTEA NOT NULL,      -- computed for ALL providers
          occurrence_index   INT  NOT NULL DEFAULT 1,  -- separates genuinely identical rows
          raw_data           JSONB NOT NULL,      -- the exact row, immutable forever
          raw_description    TEXT NOT NULL,       -- immutable; user edits never touch this
          raw_amount         BIGINT NOT NULL,     -- minor units, SIGNED (ledger convention)
          raw_currency       CHAR(3) NOT NULL REFERENCES currency(code),
          raw_date           DATE NOT NULL,
          -- Amex PDF: process date. NOT nullable in practice -- differs from
          -- raw_date on 42 of 121 rows (35%), doc 11 section 3.1. Map the two PDF
          -- date columns separately.
          raw_posting_date   DATE,
          status             TEXT NOT NULL DEFAULT 'imported'
                               CHECK (status IN ('imported','pending','posted','duplicate')),
          -- ON DELETE SET NULL, not the default RESTRICT/NO ACTION. journal_line
          -- cascades when its entry goes, but without this the entry could not
          -- be deleted at all: the FK would fire first and abort. With SET NULL
          -- a deleted entry leaves the record booked-but-orphaned, which is
          -- re-bookable -- and it keeps the cascade branch of the balance
          -- trigger live instead of dead code.
          journal_entry_id   BIGINT REFERENCES journal_entry(id) ON DELETE SET NULL,
          error_message      TEXT,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Dedupe scope: NOT (import_batch_id, ...). The fingerprint already embeds
    # account_id and occurrence_index, so scoping it to the batch made re-import
    # the same file as a second batch invisible to the index -- and re-importing
    # the same statement is the canonical dedupe case. Scoping by account_id for
    # provider_txn_id is right for the same reason: a provider reference is
    # stable across files but meaningless across accounts.
    #
    # Both are partial/index-only where uniqueness cannot be a table constraint.
    # Postgres has no partial UNIQUE table constraint; section E's inline
    # CONSTRAINT uq_sr_batch_providertxn was therefore unsatisfiable as written
    # and has been replaced by these indexes rather than left as a comment
    # describing a constraint that does not exist.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_sr_providertxn ON finance.source_record (account_id, provider_txn_id)
          WHERE provider_txn_id IS NOT NULL
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sr_fingerprint ON finance.source_record (fingerprint)"
    )
    # idx_sr_fingerprint_lookup (fingerprint, account_id) from section E is gone:
    # uq_sr_fingerprint is unique on the leading column, so it already serves
    # every lookup that index supported.
    op.execute(
        "CREATE INDEX idx_sr_account_status ON finance.source_record (account_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_sr_desc_trgm ON finance.source_record "
        "USING gin (raw_description gin_trgm_ops)"
    )
    op.execute(
        """
        CREATE INDEX idx_sr_open_pending ON finance.source_record (account_id, raw_date)
          WHERE status = 'pending' AND journal_entry_id IS NULL
        """
    )


# ---------------------------------------------------------------------------
# Merchant and category
# ---------------------------------------------------------------------------


def _merchant_and_category() -> None:
    op.execute(
        """
        CREATE TABLE finance.merchant (
          id            BIGSERIAL PRIMARY KEY,
          name          TEXT NOT NULL UNIQUE,    -- canonical: 'Albert Heijn'
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.merchant_alias (
          id            BIGSERIAL PRIMARY KEY,
          merchant_id   BIGINT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
          raw_string    TEXT NOT NULL,           -- 'ALBERT HEIJN 1234 AMSTERDAM'
          confidence    NUMERIC(3,2) NOT NULL DEFAULT 0.50,
          usage_count   INT NOT NULL DEFAULT 0,
          last_seen     DATE NOT NULL DEFAULT CURRENT_DATE,
          CONSTRAINT uq_alias_raw_string UNIQUE (raw_string)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_alias_raw ON finance.merchant_alias "
        "USING gin (raw_string gin_trgm_ops)"
    )
    op.execute(
        """
        CREATE TABLE finance.category (
          id          BIGSERIAL PRIMARY KEY,
          parent_id   BIGINT REFERENCES category(id),
          name        TEXT NOT NULL,
          kind        TEXT NOT NULL CHECK (kind IN ('expense','income','transfer','investment')),
          sort_order  INT NOT NULL DEFAULT 0,
          is_system   BOOLEAN NOT NULL DEFAULT FALSE,
          CONSTRAINT uq_category_parent_name UNIQUE (parent_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finance.category_rule (
          id                  BIGSERIAL PRIMARY KEY,
          priority            INT NOT NULL DEFAULT 100,
          account_id          BIGINT REFERENCES account(id),
          merchant_id         BIGINT REFERENCES merchant(id),
          description_pattern TEXT,              -- trigram/ILIKE match on raw_description
          category_id         BIGINT NOT NULL REFERENCES category(id),
          is_learned          BOOLEAN NOT NULL DEFAULT FALSE,
          confidence          NUMERIC(3,2) NOT NULL DEFAULT 1.00,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_rule_lookup "
        "ON finance.category_rule (merchant_id, description_pattern, priority)"
    )


# ---------------------------------------------------------------------------
# Canonical ledger — double entry
# ---------------------------------------------------------------------------


def _ledger() -> None:
    op.execute(
        """
        CREATE TABLE finance.journal_entry (
          id                 BIGSERIAL PRIMARY KEY,
          entry_date         DATE NOT NULL,
          description        TEXT,
          is_transfer        BOOLEAN NOT NULL DEFAULT FALSE,
          is_split           BOOLEAN NOT NULL DEFAULT FALSE,
          user_verified_at   TIMESTAMPTZ,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_je_date ON finance.journal_entry (entry_date DESC)")

    # transfer_match_id is declared here WITHOUT its foreign key. transfer_match
    # points at journal_line, so the pair is cyclic: inline, this table cannot be
    # created (target does not exist yet), and transfer_match cannot be created
    # first either (journal_line would not exist). The key is attached after both
    # tables exist, and then VALIDATEd, so the result is a real validated
    # constraint -- convalidated = t -- not a permanently NOT VALID one.
    op.execute(
        """
        CREATE TABLE finance.journal_line (
          id                  BIGSERIAL PRIMARY KEY,
          journal_entry_id    BIGINT NOT NULL REFERENCES journal_entry(id) ON DELETE CASCADE,
          account_id          BIGINT NOT NULL REFERENCES account(id),
          amount              BIGINT NOT NULL,        -- account currency, minor units, SIGNED
          currency            CHAR(3) NOT NULL REFERENCES currency(code),  -- snapshot of account.currency
          amount_base         NUMERIC(18,4) NOT NULL, -- EUR, signed, converted at import time
          exchange_rate       NUMERIC(20,10) NOT NULL DEFAULT 1.0,
          foreign_amount      BIGINT,                 -- original foreign amount, e.g. USD (Amex PDF)
          foreign_currency    CHAR(3) REFERENCES currency(code),
          category_id         BIGINT REFERENCES category(id),
          merchant_id         BIGINT REFERENCES merchant(id),
          transfer_match_id   BIGINT,                 -- FK added after transfer_match exists
          is_synthesized      BOOLEAN NOT NULL DEFAULT FALSE,  -- the Amex card-payment leg
          synthesized_reason  TEXT CHECK (synthesized_reason IN
                                ('card_payment','sepa_dd','investment','opening_balance')),
          security_id         BIGINT,                 -- investment seam
          units               NUMERIC(24,12),
          price_per_unit      NUMERIC(20,10),
          sort_order          INT NOT NULL DEFAULT 0
        )
        """
    )

    # Sign carries direction. Debits negative, credits positive. No `direction`
    # column.
    #
    # The Amex SOURCE convention is the inverse ("charges positive", R02:59), and
    # the Amex PDF carries no sign at all -- direction is a separate `CR` marker
    # line printed beneath the amount (doc 11 section 3.2). An adapter must flip
    # EXPLICITLY and emit already-signed minor units
    # (AmountSignConvention.SIGNED), so the normalizer does not flip twice. The
    # flip is a decision, not an implication:
    # docs/adr/0003-import-decisions-real-export.md Decision 1.
    #
    # A `CR` line separates credits from CHARGES -- not card payments from
    # refunds. Each statement's credit section mixes the monthly payment with
    # real refunds, so the payment is separated by DESCRIPTION
    # (`HARTELIJK BEDANKT VOOR UW BETALING`, 4/4 statements).
    op.execute("CREATE INDEX idx_jl_entry ON finance.journal_line (journal_entry_id)")
    # Deliberately (account_id, journal_entry_id) and NOT a denormalised
    # entry_date. Section E sketched `entry_id_journal()`, a function that does
    # not exist -- it would not parse. More importantly, a copied entry_date on
    # the line is a second source of truth for the same fact, and section G Tier 2
    # mutates entry_date IN PLACE when a pending entry merges into a posted one;
    # a denormalised copy would then silently misstate which month a transaction
    # falls in. Join to journal_entry instead.
    op.execute(
        "CREATE INDEX idx_jl_acct ON finance.journal_line (account_id, journal_entry_id)"
    )
    op.execute(
        "CREATE INDEX idx_jl_cat ON finance.journal_line (category_id) "
        "WHERE category_id IS NOT NULL"
    )
    # Uncategorized review queue -- the single most important partial index.
    op.execute(
        """
        CREATE INDEX idx_jl_uncat ON finance.journal_line (account_id, journal_entry_id)
          WHERE category_id IS NULL AND transfer_match_id IS NULL AND amount_base < 0
        """
    )
    # Unmatched outbound legs, for the transfer-matching sweep.
    op.execute(
        "CREATE INDEX idx_jl_unmatched ON finance.journal_line (account_id) "
        "WHERE transfer_match_id IS NULL AND amount_base < 0"
    )
    op.execute(
        "CREATE INDEX idx_jl_transfer ON finance.journal_line (transfer_match_id) "
        "WHERE transfer_match_id IS NOT NULL"
    )

    # TransferMatch — stored, never recomputed.
    op.execute(
        """
        CREATE TABLE finance.transfer_match (
          id                   BIGSERIAL PRIMARY KEY,
          journal_line_id_out  BIGINT NOT NULL REFERENCES journal_line(id),
          journal_line_id_in   BIGINT NOT NULL REFERENCES journal_line(id),
          match_method         TEXT NOT NULL CHECK (match_method IN
                                 ('auto_amount_date','auto_card_payment','auto_sepa_dd',
                                  'user_confirmed','user_created')),
          confidence           NUMERIC(3,2) NOT NULL DEFAULT 1.00,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          confirmed_at         TIMESTAMPTZ,
          CONSTRAINT uq_tm_pair UNIQUE (journal_line_id_out, journal_line_id_in),
          CONSTRAINT ck_tm_distinct CHECK (journal_line_id_out <> journal_line_id_in)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_tm_out ON finance.transfer_match (journal_line_id_out)"
    )
    op.execute("CREATE INDEX idx_tm_in ON finance.transfer_match (journal_line_id_in)")

    op.execute(
        """
        ALTER TABLE finance.journal_line
          ADD CONSTRAINT fk_journal_line_transfer_match
          FOREIGN KEY (transfer_match_id) REFERENCES transfer_match(id) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE finance.journal_line VALIDATE CONSTRAINT fk_journal_line_transfer_match"
    )


# ---------------------------------------------------------------------------
# Recurring — detection output, user-confirmed
# ---------------------------------------------------------------------------


def _recurring() -> None:
    op.execute(
        """
        CREATE TABLE finance.recurring_series (
          id                   BIGSERIAL PRIMARY KEY,
          account_id           BIGINT NOT NULL REFERENCES account(id),
          merchant_id          BIGINT REFERENCES merchant(id),
          category_id          BIGINT REFERENCES category(id),
          amount_base          NUMERIC(18,4) NOT NULL,
          currency             CHAR(3) NOT NULL REFERENCES currency(code),
          frequency            TEXT NOT NULL CHECK (frequency IN
                                 ('weekly','biweekly','monthly','quarterly','yearly')),
          first_date           DATE NOT NULL,
          last_date            DATE,
          detection_confidence NUMERIC(3,2),
          status               TEXT NOT NULL DEFAULT 'detected'
                               CHECK (status IN ('detected','confirmed','dismissed'))
        )
        """
    )
    # NULLS NOT DISTINCT, and it changes behaviour. merchant_id is nullable, and
    # under default NULL semantics two NULL merchants are NOT equal to each
    # other -- so the default index permits exactly the duplicate an uncategorised
    # recurring series is most likely to produce. PostgreSQL 15+ only; the
    # deployment target is 17 (docker-compose.yml pins postgres:17-alpine).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_recurring ON finance.recurring_series
          (account_id, merchant_id, amount_base, frequency, first_date) NULLS NOT DISTINCT
        """
    )


# ---------------------------------------------------------------------------
# Triggers — the invariants, enforced by the database
# ---------------------------------------------------------------------------


def _triggers() -> None:
    # updated_at with nothing to maintain it silently equals created_at forever.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.touch_journal_entry_updated_at() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at := now();
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_journal_entry_updated_at
          BEFORE UPDATE ON finance.journal_entry
          FOR EACH ROW EXECUTE FUNCTION finance.touch_journal_entry_updated_at()
        """
    )

    # Deferred constraint trigger: fires at COMMIT, when every line of the entry
    # exists. An immediate trigger would reject the first line of every entry.
    #
    # Three things the section E version got wrong, all verified on 17.11:
    #   * it resolved ONE entry id. Re-parenting a line from entry B to entry A
    #     left B holding one unbalanced line that nothing ever re-examined. Both
    #     ends are checked here.
    #   * `<> 0` rejected a genuinely balanced entry carrying a 0.0001 FX
    #     residual. The tolerance is half a cent of EUR.
    #   * with a minimum-line-count added, a cascading entry deletion raises
    #     "has 0 line(s)" unless the parent-existence skip below is there.
    #     It is there, and it is load-bearing, not defensive.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.assert_journal_entry_balances() RETURNS TRIGGER AS $$
        DECLARE
          eids BIGINT[];
          eid  BIGINT;
          tol  NUMERIC := 0.005;      -- half a cent of EUR; see note above
          s    NUMERIC;
          n    BIGINT;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            eids := ARRAY[OLD.journal_entry_id];
          ELSIF TG_OP = 'UPDATE' AND NEW.journal_entry_id IS DISTINCT FROM OLD.journal_entry_id THEN
            eids := ARRAY[OLD.journal_entry_id, NEW.journal_entry_id];   -- BOTH ends
          ELSE
            eids := ARRAY[NEW.journal_entry_id];
          END IF;

          FOREACH eid IN ARRAY eids LOOP
            CONTINUE WHEN eid IS NULL;
            CONTINUE WHEN NOT EXISTS (SELECT 1 FROM finance.journal_entry WHERE id = eid);

            SELECT count(*), COALESCE(SUM(amount_base), 0) INTO n, s
              FROM finance.journal_line WHERE journal_entry_id = eid;

            IF n < 2 THEN
              RAISE EXCEPTION 'JournalEntry % has % line(s); a journal entry needs at least 2', eid, n
                USING ERRCODE = '23514';
            END IF;

            IF abs(s) > tol THEN
              RAISE EXCEPTION 'JournalEntry % does not balance (base sum = %)', eid, s
                USING ERRCODE = '23514';
            END IF;
          END LOOP;

          RETURN NULL;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_journal_balances
          AFTER INSERT OR UPDATE OR DELETE ON finance.journal_line
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION finance.assert_journal_entry_balances()
        """
    )

    # Companion to the trigger above, and it is not redundant. The balance
    # trigger only fires on journal_line events, so an entry created with no
    # lines at all committed silently -- there is no line event to react to.
    # This one fires on the ENTRY instead.
    #
    # The existence skip is required: an entry can be created and deleted inside
    # one transaction (a rolled-back import, a corrected duplicate), and without
    # the skip that legitimate sequence is reported as a violation.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.assert_journal_entry_has_lines() RETURNS TRIGGER AS $$
        DECLARE
          n BIGINT;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM finance.journal_entry WHERE id = NEW.id) THEN
            RETURN NULL;
          END IF;

          SELECT count(*) INTO n
            FROM finance.journal_line WHERE journal_entry_id = NEW.id;

          IF n < 2 THEN
            RAISE EXCEPTION 'JournalEntry % has % line(s); a journal entry needs at least 2', NEW.id, n
              USING ERRCODE = '23514';
          END IF;

          RETURN NULL;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_journal_entry_has_lines
          AFTER INSERT ON finance.journal_entry
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION finance.assert_journal_entry_has_lines()
        """
    )

    # raw_data_immutable, at the database. BEFORE, so the write is refused
    # before it happens rather than compensated afterwards.
    #
    # It inspects exactly two fields, so status, journal_entry_id,
    # error_message and occurrence_index stay writable -- and they must stay
    # writable, because the pipeline advances a record through
    # imported -> pending -> posted without touching the raw evidence.
    #
    # to_jsonb(NEW) -> field rather than NEW.<field>: the field list is a loop
    # variable, and dynamic field access is the point.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finance.assert_source_record_raw_immutable() RETURNS TRIGGER AS $$
        DECLARE field TEXT;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION USING
              ERRCODE = '23514',
              MESSAGE = format('raw_data_immutable: DELETE on source_record id=%s is forbidden', OLD.id),
              HINT    = 'The raw side is append-only. Reverse in the journal; never delete raw evidence.';
          END IF;
          FOREACH field IN ARRAY ARRAY['raw_data', 'raw_description'] LOOP
            IF to_jsonb(NEW) -> field IS DISTINCT FROM to_jsonb(OLD) -> field THEN
              RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = format('raw_data_immutable: %s on source_record id=%s is immutable', field, OLD.id),
                HINT    = 'Corrections belong in journal_entry/journal_line, never in the raw record.';
            END IF;
          END LOOP;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_source_record_raw_immutable
          BEFORE UPDATE OR DELETE ON finance.source_record
          FOR EACH ROW EXECUTE FUNCTION finance.assert_source_record_raw_immutable()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_source_record_raw_immutable ON finance.source_record"
    )
    op.execute("DROP FUNCTION IF EXISTS finance.assert_source_record_raw_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_journal_entry_has_lines ON finance.journal_entry"
    )
    op.execute("DROP FUNCTION IF EXISTS finance.assert_journal_entry_has_lines()")
    op.execute("DROP TRIGGER IF EXISTS trg_journal_balances ON finance.journal_line")
    op.execute("DROP FUNCTION IF EXISTS finance.assert_journal_entry_balances()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_journal_entry_updated_at ON finance.journal_entry"
    )
    op.execute("DROP FUNCTION IF EXISTS finance.touch_journal_entry_updated_at()")

    op.execute("DROP TABLE IF EXISTS finance.recurring_series")
    op.execute("DROP TABLE IF EXISTS finance.source_record")
    op.execute("DROP TABLE IF EXISTS finance.transfer_match")
    op.execute("DROP TABLE IF EXISTS finance.journal_line")
    op.execute("DROP TABLE IF EXISTS finance.journal_entry")
    op.execute("DROP TABLE IF EXISTS finance.category_rule")
    op.execute("DROP TABLE IF EXISTS finance.category")
    op.execute("DROP TABLE IF EXISTS finance.merchant_alias")
    op.execute("DROP TABLE IF EXISTS finance.merchant")
    op.execute("DROP TABLE IF EXISTS finance.import_batch")
    op.execute("DROP TABLE IF EXISTS finance.provider_account_link")
    op.execute("DROP TABLE IF EXISTS finance.account")
    op.execute("DROP TABLE IF EXISTS finance.institution")
    op.execute("DROP TABLE IF EXISTS finance.exchange_rate")
    op.execute("DROP TABLE IF EXISTS finance.currency")

    # pg_trgm is NOT dropped: it is installed by the compose bootstrap and shared.
    # Dropping it here would make downgrade fail with a dependency error anyway.
