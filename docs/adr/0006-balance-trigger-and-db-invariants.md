# ADR 0006: the balance trigger is deferred, tolerant, and EUR-based

**Date:** 2026-10-03
**Status:** Accepted
**Decision Drivers:** a two-line entry must commit; an unbalanced one must be rejected **at commit**;
a valid entry carrying an FX residual must not be rejected; the guarantees must be enforceable by
the database rather than by convention.

## Context

§E of `docs/ARCHITECTURE-PROPOSAL.md` specifies a deferred constraint trigger, a EUR-base
zero-sum, and a `raw_data_immutable` DB trigger. Each of those three was checked against a live
PostgreSQL 17.11 before being written into the migration, and each had a defect that only showed
up when executed.

### 1. A per-row check cannot work; §E's deferred one still had holes

The acceptance criterion is that an unbalanced entry is rejected at COMMIT. A row-level trigger
would inspect the sum before the second leg exists and reject every valid entry, so deferral is
mandatory — `DEFERRABLE INITIALLY DEFERRED`, verified. But §E's version resolves exactly one entry
id via `COALESCE(NEW.journal_entry_id, OLD.journal_entry_id)`, which means a re-parent validates
only the **destination**. Verified exploit: re-parent a leg, delete the destination entry, and
commit with the source entry holding one leg and a non-zero base sum.

Separately, an entry with **zero** legs commits silently, because no `journal_line` event ever
fires to trigger a check.

Note the `EXISTS (SELECT 1 FROM journal_entry ...)` guard is **semantically inert** for its stated
purpose: cascading entry deletion passes without it, because zero surviving lines sum to NULL and
`COALESCE` turns that into 0. It must nevertheless be *kept* once a minimum-legs check exists, or
cascade breaks outright.

### 2. `<> 0` rejects valid entries

`amount` is signed `BIGINT` minor units; `amount_base` is `NUMERIC(18,4)` EUR. Each leg is rounded
independently, so an entry that balances to the cent can carry a small residual. Verified: a
genuinely balanced entry with a `0.0001` residual was rejected outright.

### 3. Per-currency zero-sum would delete a feature

It is tempting to enforce zero-sum per currency. `account_nature` is `(asset|liability|equity)`
with **no income and no expense** — there is nowhere to post the counter-leg of a purchase except
another balance-sheet bucket. So no single currency can sum to zero, and a per-currency rule would
forbid FX purchases and FX-denominated liabilities (the Amex USD card is an explicit v1 provider).

### 4. A row trigger cannot fully enforce `raw_data_immutable`

A `BEFORE UPDATE` trigger cannot distinguish "the statement never mentioned `raw_data`" from "the
statement assigned it its current value" — verified, an identical-value write sails through. The
mechanism that does satisfy it is column privileges: `REVOKE UPDATE ON source_record` followed by
`GRANT UPDATE (status, journal_entry_id, error_message, occurrence_index)`. Note that
`REVOKE UPDATE (raw_data)` alone is a **silent no-op** while the role holds table-level `UPDATE`.

### 5. `no_cross_schema_fk` is enforced by an event trigger, not by grants

`ARCHITECTURE.md` §4 promises "Postgres grants/role setup" as the DB-level half. It is not one:
`GRANT ALL ON SCHEMA core TO lifeos` plus `GRANT ALL ON SCHEMA finance TO lifeos` means a role
owning both can create a cross-schema foreign key freely — and `lifeos` is a superuser, so a
USAGE revoke would bind nobody either. The actual enforcement is the `trg_no_cross_schema_fk`
event trigger on `ddl_command_end`, installed by `backend/db_bootstrap.sql`: it aborts any DDL
that creates a cross-schema FK, in the DDL's own transaction, superuser included. (An earlier
static regex in `invariants.yaml` claimed this job; it was deleted in commit `8bdfd57`.)

## Decision

1. **Deferred constraint trigger, validating both ends on re-parent**, plus a `count(*) >= 2`
   check, plus a companion deferred trigger on `journal_entry` for the zero-leg case. Full SQL is
   in `backend/finance/alembic/versions/0001_finance_ledger_schema.py`.
2. **Compare with `abs(SUM) > 0.005`**, half a cent of EUR — and separately, drive the residual to
   exactly zero at write time by recomputing the largest-magnitude leg as `-SUM(others)`. The
   tolerance is a safety net for hand-written and third-party inserts, not the mechanism.
3. **Zero-sum on the EUR base only.** Per-currency is rejected for the structural reason above.
4. **`raw_data_immutable` enforced by trigger in M1**, with the column-privilege layer against a
   separate `lifeos_app` role deferred as defence in depth. The trigger blocks every actual tamper,
   including under superuser; only the identical-value write passes, and that changes nothing.

## Consequences

- **The triggers are not a complete integrity boundary, and no document may claim otherwise.**
  `TRUNCATE journal_line, journal_entry` bypasses them entirely (verified, exit 0). A caller who
  can truncate can destroy the ledger while every invariant still reports healthy.
- **`AUTOCOMMIT` silently converts the deferred check back to a per-row one.** With no explicit
  `BEGIN`, a two-line entry fails on its first leg. Every journal write must sit inside an explicit
  transaction block.
- **`SET CONSTRAINTS ALL IMMEDIATE` inside a transaction breaks every multi-line write.** It is
  transaction-scoped and is ignored outside a transaction block, so it cannot become a sticky
  session setting — but the obvious "let's make it immediate before a bulk load" optimisation
  destroys the ledger's core guarantee.
- **Blocking deletes has a consequence worth stating deliberately:** because `import_batch` deletes
  cascade to `source_record`, and `source_record` deletion is forbidden, deleting an import batch
  is now impossible. That is the correct outcome for raw evidence, but it is a decision, not an
  accident.
- **`source_record.journal_entry_id` is `ON DELETE SET NULL`,** and `status` is reset, so a record
  whose entry was removed can be re-booked. Without it, entry deletion is impossible in practice
  and the cascade branch of the balance trigger is dead code.
- **Dedupe indexes are scoped `(account_id, provider_txn_id)` and `(fingerprint)`,** not by
  `import_batch_id` as §E specified. §E's scoping left the entire dedupe layer inert across
  re-imports (verified: re-importing the same file as a second batch was accepted). `occurrence_index`
  still disambiguates genuinely identical rows within one file.
- **The privilege-layer and role-separation work is tracked separately**, not folded into M1: it is
  a deployment and compose change, and the guarantee it adds is redundant with the trigger for
  every case that actually threatens the data.
- **A reversal convention is not yet defined.** With entries and lines deletable, the only
  correction path is delete-and-reinsert, which destroys the audit trail. The intended rule —
  posted entries are immutable, corrections are reversing entries — is recorded here and enforced
  in a later milestone. `journal_entry.updated_at` is maintained by trigger; without one it would
  silently equal `created_at` forever.