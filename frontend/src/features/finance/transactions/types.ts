/* ── Transactions ──────────────────────────────────────────────────────────
 * Hand-maintained contract for `GET/POST /transactions`,
 * `GET /transactions/{id}`, `GET /transactions/uncategorized`,
 * `PATCH /transactions/{id}` and `DELETE /transactions/{id}`.
 *
 * Mirrors `finance.public.TransactionSummary` and
 * `finance.api.schemas.ManualTransactionRequest` / `ManualTransactionUpdate`.
 * There is no generated client, so this file is half of a contract with the
 * backend and has to be read against it whenever either side changes.
 *
 * `raw_amount` is SIGNED INTEGER MINOR UNITS: -4050 is −40.50, not −4050.00.
 * The exponent comes from the currency table server-side and is mirrored for
 * display in `@/lib/money`. Every id below is `int` — BIGSERIAL, never a
 * string. */
import { z } from "zod";

/** A calendar day, `YYYY-MM-DD`. NOT an ISO datetime: `raw_date` is a
 * `datetime.date`, so `2026-10-01`, with no time and no zone. */
const IsoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD");

/** Mirrors `finance.public.TransactionStatus`. */
export const TransactionStatusSchema = z.enum([
  "imported",
  "pending",
  "posted",
  "duplicate",
]);
export type TransactionStatus = z.infer<typeof TransactionStatusSchema>;

export const TransactionSummarySchema = z.object({
  id: z.number().int(),
  account_id: z.number().int(),
  /** The SHA-256 dedupe fingerprint as hex.
   *
   * The API documents 64 characters — that is what `compute_fingerprint`
   * returns, and what `FingerprintResult.fingerprint` carries. The column
   * itself is BYTEA, though, so a row written outside the pipeline can hold
   * any length at all, and one of them already exists in a real database:
   * `hex()` of the eight bytes "deadbeef". Pinned to hex and NOT to 64
   * characters, because a short fingerprint is a fact about one row and must
   * not turn the whole list into an error. */
  fingerprint: z.string().regex(/^[0-9a-f]+$/),
  raw_description: z.string(),
  raw_amount: z.number().int(),
  raw_currency: z.string().length(3),
  raw_date: IsoDate,
  status: TransactionStatusSchema,
  journal_entry_id: z.number().int().nullable(),
  transfer_match_id: z.number().int().nullable(),
  category_id: z.number().int().nullable(),
  /** True ONLY on the `PATCH` response that taught the system — the correction
   * was stored as a learned rule in the same transaction. Every other response
   * carries False, because nothing was learned there.
   *
   * So this flag is the answer to "did the correction stick for next time", and
   * it is not derivable from anything else in the payload: the same category
   * lands either way. The screen that asks "remember this payee" reports what
   * came back rather than what it asked for, because the two differ — a
   * description that normalises to nothing is refused with a 422, and a UI that
   * claimed success would be claiming a lesson nobody was taught. */
  learned: z.boolean().default(false),
});
export type TransactionSummary = z.infer<typeof TransactionSummarySchema>;

/** The body of `POST /transactions`.
 *
 * `amount` is a STRING of major units and it is SIGNED: `"-40.50"` is money
 * out, `"40.50"` is money in. A JSON number would arrive as a float, and the
 * ledger takes no floats — the wire format has to make that impossible rather
 * than merely discouraged. `counter_account_id` is omitted here: the server
 * resolves the seeded system expense account, and naming one is a later
 * question than "record this expense".
 */
export const ManualTransactionRequestSchema = z.object({
  account_id: z.number().int().min(1),
  description: z.string().min(1).max(500),
  amount: z.string().min(1),
  currency: z.string().regex(/^[A-Z]{3}$/),
  booked_date: IsoDate,
});
export type ManualTransactionRequest = z.infer<typeof ManualTransactionRequestSchema>;

/** The body of `PATCH /transactions/{id}`: category and accounting date, and
 * nothing else. The raw side is frozen by the `raw_data_immutable` trigger, so
 * an amount, a currency, a description and the booking date cannot be edited —
 * a correction is a reversal, a new balanced entry, not an edit of this one.
 *
 * An absent field means "leave alone", which is why this is a PATCH. */
export const ManualTransactionUpdateSchema = z.object({
  category_id: z.number().int().min(1).nullable().optional(),
  entry_date: IsoDate.nullable().optional(),
  /** Teach the system this correction, as well as recording it.
   *
   * Only honoured alongside a `category_id` — the server stores a learned rule
   * keyed on the record's own frozen `raw_description`, so there is nothing to
   * attach a lesson to without one. Deliberately ABSENT by default rather than
   * `false`: a correction is a correction, and silently teaching the system is
   * the one thing on this screen a user did not ask for. The flag is sent only
   * when the checkbox is ticked. */
  learn: z.boolean().optional(),
});
export type ManualTransactionUpdate = z.infer<typeof ManualTransactionUpdateSchema>;

export const TransactionListSchema = z.array(TransactionSummarySchema);
