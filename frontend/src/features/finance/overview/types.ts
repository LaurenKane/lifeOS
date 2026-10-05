/* ── Overview ──────────────────────────────────────────────────────────────
 * The hand-maintained contract for the three read-only aggregates this page is
 * built on: `GET /analytics/net-worth`, `/analytics/spend-by-category` and
 * `/analytics/cashflow`.
 *
 * There is no generated client — that generation is unimplemented
 * (ARCHITECTURE.md §7) — so these schemas ARE the contract, and they are half of
 * a contract with the backend and have to be read against it whenever either side
 * changes. The mirrors are `finance.api.schemas.NetWorthPoint`,
 * `SpendByCategoryPoint` and `CashflowBucket`.
 *
 * Every money field is a SIGNED INTEGER of minor units. `z.number().int()` is not
 * decoration here: an amount that arrives as `1234.56` is a wire-format change
 * upstream, and rendering it would print two decimals of float noise into a
 * column that is supposed to be exact. It has to fail here instead. */
import { z } from "zod";

/** A calendar day, `YYYY-MM-DD`. `entry_date` is a `datetime.date`, so no time
 * and no zone — an ISO datetime would let one slip between the API and a
 * `<input type="date">`. */
const IsoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "expected YYYY-MM-DD");

/** Mirrors `finance.api.schemas.NetWorthPoint`.
 *
 * `net_worth` is assets PLUS liabilities — liabilities are already stored
 * negative, and subtracting them again would report money owed as money held. */
export const NetWorthPointSchema = z.object({
  date: IsoDate,
  net_worth: z.number().int(),
});
export type NetWorthPoint = z.infer<typeof NetWorthPointSchema>;
export const NetWorthSeriesSchema = z.array(NetWorthPointSchema);

/** Mirrors `finance.api.schemas.SpendByCategoryPoint`.
 *
 * `kind` mirrors `finance.public.CategoryKind`. It is closed on both sides: an
 * unlisted kind fails here rather than rendering as an uncategorised bar.
 *
 * `amount` is SIGNED — expenses negative, income positive — which is the
 * ledger's convention everywhere else, and the reason this page must not
 * re-sign it. */
const CategoryKind = z.enum(["income", "expense", "transfer"]);

export const SpendByCategoryPointSchema = z.object({
  category_id: z.number().int(),
  category_name: z.string(),
  kind: CategoryKind,
  amount: z.number().int(),
});
export type SpendByCategoryPoint = z.infer<typeof SpendByCategoryPointSchema>;
export const SpendByCategorySchema = z.array(SpendByCategoryPointSchema);

/** Mirrors `finance.api.schemas.CashflowBucket`.
 *
 * `income` and `expense` are both POSITIVE magnitudes while `net` keeps its
 * sign. That asymmetry is deliberate upstream and is the one place these three
 * schemas do not share a convention, so it is restated here rather than
 * normalised: normalising it would make this client disagree with the server
 * about what `expense: 0` means.
 *
 * `period` is `YYYY-MM-DD` for day, the Monday for week, `YYYY-MM` for month.
 * It is a string on the wire and stays a string here; only this page's own
 * labelling knows which of the three it asked for.
 *
 * Note what is NOT in this schema: there is no zero bucket. Upstream omits a
 * period with no movement rather than emitting one, so an absent month is real
 * data and this client renders it as an absent month. Inventing a `0/0/0` row
 * would put a bar in a chart for a month in which nothing happened. */
export const CashflowBucketSchema = z.object({
  period: z.string(),
  income: z.number().int(),
  expense: z.number().int(),
  net: z.number().int(),
});
export type CashflowBucket = z.infer<typeof CashflowBucketSchema>;
export const CashflowSchema = z.array(CashflowBucketSchema);