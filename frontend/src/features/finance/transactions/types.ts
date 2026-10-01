/* ── Transaction types (stub) ─────────────────────────────────────── */
import { z, type ZodType } from "zod";

export const TransactionSchema: ZodType = z.object({
  id: z.string(),
  description: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  date: z.string().datetime(),
});

export type Transaction = z.infer<typeof TransactionSchema>;