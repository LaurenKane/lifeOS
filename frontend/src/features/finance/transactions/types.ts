/* ── Transaction types (stub) ─────────────────────────────────────── */
import { z } from "zod";

/* Money is signed integer MINOR units (e.g. cents), never a float.
 * `currency` is an ISO-4217 code whose decimal count is the
 * authoritative exponent — see ARCHITECTURE.md §6. */
export const TransactionSchema = z.object({
  id: z.string(),
  description: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  date: z.string().datetime(),
});

export type Transaction = z.infer<typeof TransactionSchema>;
