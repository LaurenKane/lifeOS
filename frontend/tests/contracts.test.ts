/**
 * The hand-maintained schemas, asserted directly.
 *
 * There is no generated client (`scripts/generate_types.py` emits no real
 * types), so these zod schemas ARE the contract, and the only thing standing
 * between a server change and a silently wrong number in an amount column.
 *
 * Two of the cases below came out of running this UI against a real migrated
 * Postgres rather than a fixture: a `source_record` row written outside the
 * pipeline held a fingerprint of `hex("deadbeef")` — eight bytes, sixteen hex
 * characters — and the list refused to render because the schema pinned the
 * documented length of 64. One odd row must not take the page down.
 */
import { describe, expect, it } from "vitest";
import { AccountSummarySchema } from "@/features/finance/accounts/types";
import {
  ManualTransactionRequestSchema,
  TransactionSummarySchema,
} from "@/features/finance/transactions/types";

const TRANSACTION = {
  id: 7,
  account_id: 1,
  fingerprint: "a4334bdd8e519b410838e9a6ba8e64fcec946a58c3488c53e4b7a6d1dd620f2f",
  raw_description: "JUMBO 4321 AMSTERDAM",
  raw_amount: -4050,
  raw_currency: "EUR",
  raw_date: "2026-10-01",
  status: "posted",
  journal_entry_id: 9,
  transfer_match_id: null,
  category_id: null,
} as const;

describe("TransactionSummary — the contract with finance.public", () => {
  it("accepts what the API actually returns", () => {
    expect(TransactionSummarySchema.safeParse(TRANSACTION).success).toBe(true);
  });

  it("accepts a short hex fingerprint, because the column is BYTEA", () => {
    // Observed in a real database: `hex()` of the bytes "deadbeef".
    const short = { ...TRANSACTION, fingerprint: "6465616462656566" };
    expect(TransactionSummarySchema.safeParse(short).success).toBe(true);
  });

  it("still refuses a fingerprint that is not hex at all", () => {
    expect(
      TransactionSummarySchema.safeParse({ ...TRANSACTION, fingerprint: "not-hex" })
        .success,
    ).toBe(false);
  });

  it("insists an id is an int, not a string", () => {
    expect(TransactionSummarySchema.safeParse({ ...TRANSACTION, id: "7" }).success).toBe(false);
  });

  it("insists an amount is signed INTEGER minor units, never a float", () => {
    expect(TransactionSummarySchema.safeParse({ ...TRANSACTION, raw_amount: -40.5 }).success).toBe(
      false,
    );
    expect(TransactionSummarySchema.safeParse({ ...TRANSACTION, raw_amount: -4050 }).success).toBe(
      true,
    );
  });

  it("insists a date is a calendar day, not a datetime", () => {
    // `raw_date` is a `datetime.date`. Accepting a timestamp here would let a
    // timezone slip between the API and the `<input type="date">`.
    expect(
      TransactionSummarySchema.safeParse({ ...TRANSACTION, raw_date: "2026-10-01T00:00:00Z" })
        .success,
    ).toBe(false);
    expect(
      TransactionSummarySchema.safeParse({ ...TRANSACTION, raw_date: "2026-10-01" }).success,
    ).toBe(true);
  });

  it("accepts a nullable journal entry and category, and requires nothing for them", () => {
    expect(
      TransactionSummarySchema.safeParse({
        ...TRANSACTION,
        journal_entry_id: null,
        category_id: null,
      }).success,
    ).toBe(true);
  });
});

describe("ManualTransactionRequest — what the form is allowed to send", () => {
  it("carries the amount as a string", () => {
    // A JSON number here would arrive at the server as a float, which this
    // ledger does not accept anywhere.
    const base = {
      account_id: 1,
      description: "JUMBO 4321 AMSTERDAM",
      currency: "EUR",
      booked_date: "2026-10-01",
    };
    expect(ManualTransactionRequestSchema.safeParse({ ...base, amount: "-40.50" }).success).toBe(
      true,
    );
    expect(ManualTransactionRequestSchema.safeParse({ ...base, amount: -40.5 }).success).toBe(false);
  });

  it("does not carry an id, a status or a counter account the form cannot justify", () => {
    const parsed = ManualTransactionRequestSchema.parse({
      account_id: 1,
      description: "x",
      amount: "-1.00",
      currency: "EUR",
      booked_date: "2026-10-01",
    });
    expect(Object.keys(parsed).sort()).toEqual([
      "account_id",
      "amount",
      "booked_date",
      "currency",
      "description",
    ]);
  });
});

describe("AccountSummary", () => {
  it("requires a closed account_type and account_nature", () => {
    const account = {
      id: 1,
      name: "Checking",
      currency: "EUR",
      account_type: "checking",
      account_nature: "asset",
      is_active: true,
      is_hidden: false,
      sort_order: 0,
    } as const;
    expect(AccountSummarySchema.safeParse(account).success).toBe(true);
    // "expense" is not an account type in this schema and never will be: the
    // contra-leg of an expense is an `equity` row.
    expect(
      AccountSummarySchema.safeParse({ ...account, account_type: "expense" }).success,
    ).toBe(false);
  });
});
