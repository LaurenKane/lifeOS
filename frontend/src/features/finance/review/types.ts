/* ── Transfer review queue ──────────────────────────────────────────────────
 * Hand-maintained contract for the transfer review queue:
 *
 *   GET  /v1/review/transfers                  → TransferReviewItem[]
 *   POST /v1/review/transfers/{id}/confirm     ← { candidate_journal_line_id }
 *   POST /v1/review/transfers/{id}/reject
 *   POST /v1/review/transfers/{id}/ignore
 *   GET  /v1/review/transfers/stats            → TransferReviewStats
 *
 * There is no generated client (ARCHITECTURE.md §7), so this file and the
 * backend's review router are two halves of one contract, read against each
 * other whenever either side changes. Wire names are the server's snake_case
 * throughout, exactly as `features/finance/transactions/types.ts` keeps them:
 * a converter would be a second place for a field name to be wrong.
 *
 * MONEY IS SIGNED INTEGER MINOR UNITS. `amount_minor: -5000` is fifty euros
 * out, never −5000.00 and never the float −5000.0. The exponent comes from the
 * currency table server-side and is mirrored for display in `@/lib/money`.
 * That rule is the whole reason a transfer review is safe: the one comparison
 * on this page is "are these two the same amount", and it is made on integers.
 *
 * An outbound leg and a candidate leg are the same SHAPE, which is why
 * `ReviewLegSchema` is written once and `ReviewCandidateSchema` extends it.
 * A candidate only adds the matcher's own score. */
import { z } from "zod";

/** A calendar day, `YYYY-MM-DD`. NOT an ISO datetime: `booked_date` is a
 * `datetime.date`, so `2026-09-28`, with no time and no zone. */
const IsoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD");

/** Mirrors the backend's review reasons — the two reasons an own-account
 * transfer is handed to a person instead of guessed at. Closed on both sides:
 * a third reason would be a decision this build cannot phrase, so it fails
 * here rather than rendering as an unlabelled row. */
export const ReviewReasonSchema = z.enum(["multi_candidate", "low_confidence"]);
export type ReviewReason = z.infer<typeof ReviewReasonSchema>;

/** The three decisions the queue offers. Mirrors the backend's
 * `ReviewDecision`; also the last path segment of each write. */
export const ReviewDecisionSchema = z.enum(["confirm", "reject", "ignore"]);
export type ReviewDecision = z.infer<typeof ReviewDecisionSchema>;

/** One side of the question: a posted journal line, as the matcher sees it.
 *
 * `account_name` travels with the line because the judgement this page exists
 * for is partly a name — "Rabobank Current" against "Revolut" says own-account
 * transfer at a glance, and two ids do not.
 *
 * `journal_line_id` is the id a confirmation names. It is the ledger's row, so
 * it is an int (BIGSERIAL) and never a string. */
export const ReviewLegSchema = z.object({
  journal_line_id: z.number().int(),
  description: z.string(),
  amount_minor: z.number().int(),
  currency: z.string().length(3),
  booked_date: IsoDate,
  account_id: z.number().int(),
  account_name: z.string(),
});
export type ReviewLeg = z.infer<typeof ReviewLegSchema>;

/** A possible counterpart for the outbound leg.
 *
 * `confidence` is the matcher's own score and is left as the string the server
 * sent. It is not an enum here: this build has not been told the vocabulary,
 * and refusing a queue over a score word it has never seen would take the whole
 * screen down over one row. Whatever arrives is shown as it arrived. */
export const ReviewCandidateSchema = ReviewLegSchema.extend({
  confidence: z.string(),
});
export type ReviewCandidate = z.infer<typeof ReviewCandidateSchema>;

/** One queue item: the outbound leg, the candidates for it, and why it is
 * here. An empty `candidates` is a real case — the nightly sweep finds an
 * unmatched outbound leg whose other half has not been imported yet — so the
 * array is never assumed to be non-empty. */
export const TransferReviewItemSchema = z.object({
  id: z.number().int(),
  outbound: ReviewLegSchema,
  candidates: z.array(ReviewCandidateSchema),
  reason: ReviewReasonSchema,
  created_at: z.string(),
});
export type TransferReviewItem = z.infer<typeof TransferReviewItemSchema>;

export const TransferReviewListSchema = z.array(TransferReviewItemSchema);

/** How deep the queue is and why. Reported rather than hidden: a queue that
 * grows silently is how a review workflow stops being used at all. */
export const TransferReviewStatsSchema = z.object({
  multi_candidate: z.number().int(),
  low_confidence: z.number().int(),
  total: z.number().int(),
});
export type TransferReviewStats = z.infer<typeof TransferReviewStatsSchema>;

/** The body of `POST /review/transfers/{id}/confirm`.
 *
 * `candidate_journal_line_id` is nullable, and that null is a real answer:
 * confirming an item the matcher found no candidate for clears the item
 * without naming a line to pair. `reject` and `ignore` take no body at all. */
export const ConfirmTransferRequestSchema = z.object({
  candidate_journal_line_id: z.number().int().nullable(),
});
export type ConfirmTransferRequest = z.infer<typeof ConfirmTransferRequestSchema>;
