"""The import pipeline's own bookkeeping: import_batch, source_record.

Two tables on opposite sides of the system.

`import_batch` is a unit of work: one run of the pipeline, with its status and
its statistics. It is deliberately NOT the row's account - one file can cover
two, and a Revolut statement proves it.

`source_record` is the raw side: what the bank actually said, kept byte for byte
so the whole pipeline can be replayed from it (M6) and every derived value can
be audited against it. `raw_data` and `raw_description` are immutable, and that
is enforced by the database, not by convention - see the trigger in migration
0001 and the `raw_data_immutable` invariant.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column

__all__ = ["ImportBatch", "SourceRecord"]


class ImportBatch(Base):
    """One run of the import pipeline against one account.

    `account_id` here is a convenience, not the row's owner: `source_record`
    carries the authoritative account per row. A batch may therefore be NULL on
    `account_id` while every row under it has one, which is what makes "one
    file, two accounts, two batches" expressible without inventing a fake
    account to hang the batch on.
    """

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institution.id"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True
    )

    # v1 list: docs/adr/0002-import-provider-enum.md. `amex_csv` is retired,
    # `revolut_csv` is deferred, `google_wallet` is excluded. `rabobank_pdf` and
    # `revolut_pdf` are declared but have no FileAdapter yet: listed, not
    # uploadable. A CHECK rather than an Enum so that list can change without a
    # table rewrite.
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    import_method: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")

    source_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CHAR(64), the width of a SHA-256 hex digest. Fixed width so a truncated
    # or padded value is still comparable, and so the dedupe key can be indexed
    # cheaply.
    source_checksum: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    # gzipped if large; NEVER removed. `raw_payload` is what a replay reads, and
    # `object` rather than `dict` because its shape is the provider's choice -
    # a gzipped CSV is a string, an Enable Banking payload is an object.
    # `disallow_any_explicit` in pyproject.toml rules `dict[str, Any]` out.
    raw_payload: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    # The per-section account ids the upload actually used (Revolut
    # Account/Deposit), lowercased section name -> local account id. NULL for
    # single-account providers, where no such decision exists to remember. It
    # is what replay reads to re-attribute a multi-section file exactly as the
    # import did, instead of re-asking a question the user already answered.
    section_account_ids: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    stats: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # TIMESTAMPTZ, explicitly typed: `Mapped[datetime]` alone renders TIMESTAMP
    # WITHOUT TIME ZONE, which silently loses the offset. An import that runs
    # over a DST boundary must not produce timestamps that sort wrongly, and a
    # 'pending' batch is compared against "now" from more than one timezone.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN "
            "('enable_banking','amex_pdf','rabobank_pdf','revolut_pdf','manual')",
            name="provider",
        ),
        CheckConstraint(
            "import_method IN ('api','csv','pdf','manual')",
            name="import_method",
        ),
        # 'partial' is not 'completed'. A statement whose rows are half booked
        # is the normal outcome of a real import, and collapsing it into
        # 'completed' is how a silently short statement becomes invisible.
        CheckConstraint(
            "status IN ('pending','processing','completed','failed','partial')",
            name="status",
        ),
    )


class SourceRecord(Base):
    """One row as the bank said it, plus the fingerprint that identifies it.

    Everything from `raw_data` down is evidence, and evidence is append-only.
    The pipeline advances `status` from 'imported' to 'pending' to 'posted'
    without touching it; the `raw_data_immutable` trigger in migration 0001
    refuses an UPDATE to `raw_data` or `raw_description` and refuses a DELETE
    outright, so a bug in a service cannot quietly rewrite history.

    `status`, `occurrence_index`, `journal_entry_id` and `error_message` are
    NOT protected by that trigger, precisely because the pipeline must be able
    to advance them.
    """

    __tablename__ = "source_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: a batch's rows have no standing without the batch, and the whole
    # point of the raw side is that it is rebuilt by replaying the source file.
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    # Enable Banking's entry_reference. NULL on every v1 PDF path: Amex IDs
    # change between statements, and Rabobank's End-to-End ID is unusable
    # (docs/adr/0003-import-decisions-real-export.md). A nullable provider key is
    # not a defect,
    # it is the honest description of four of the five providers.
    provider_txn_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # BYTEA, not a hex TEXT column: the fingerprint is a SHA-256 digest and
    # storing the 32 raw bytes rather than 64 hex characters halves the index.
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # INT, explicitly. Separates genuinely identical rows within one file - two
    # identical coffees at the same time on the same card are two transactions,
    # not one, and collapsing them loses money.
    occurrence_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    # The exact row, immutable forever.
    raw_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    # Immutable; a user correction never touches this. It lives here so that
    # "what did the statement say" is answerable without consulting
    # journal_line, which the user is allowed to have edited.
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    # Minor units, SIGNED, in the ledger's own convention: debits negative,
    # credits positive. A BigInteger, not a Decimal - see ARCHITECTURE.md §6.
    raw_amount: Mapped[int] = mapped_column(nullable=False)
    raw_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    raw_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Amex PDF's process date. Distinct from raw_date on 42 of 121 rows in a
    # real statement, so the two date columns are mapped separately rather than
    # collapsed into one.
    raw_posting_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="imported")
    # SET NULL, not RESTRICT and not CASCADE. CASCADE would delete the raw
    # evidence when a journal entry is deleted, which `raw_data_immutable`
    # forbids by intent; RESTRICT would make a journal entry impossible to
    # delete at all, because the FK would fire first and abort. SET NULL leaves
    # the record booked-but-orphaned, which is re-bookable, and keeps the
    # cascade branch of the balance trigger live rather than dead code.
    journal_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "status IN ('imported','pending','posted','duplicate')",
            name="status",
        ),
    )

    # NOT modelled - all three are unique indexes created by migration 0001:
    #   uq_sr_providertxn (account_id, provider_txn_id)
    #       WHERE provider_txn_id IS NOT NULL
    #   uq_sr_fingerprint (fingerprint)
    #   idx_sr_account_status (account_id, status)
    #   idx_sr_open_pending (account_id, raw_date)
    #       WHERE status = 'pending' AND journal_entry_id IS NULL
    #   idx_sr_desc_trgm USING gin (raw_description gin_trgm_ops)
    #
    # `uq_sr_fingerprint` is the dedupe invariant and the reason this table
    # exists twice: the same statement imported again must produce zero new
    # rows. Postgres has no partial UNIQUE *table constraint*, so the migration
    # expresses all of these as indexes; re-expressing them here would mean
    # inventing table constraints the database does not have.
