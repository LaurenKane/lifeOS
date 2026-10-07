/* Budget types — the backend's `BudgetSummary`, mirrored.
 *
 * `category_id` rides on the wire because two branches of the category tree
 * can share a name; the actual-figures lookup keys on it. */
import { z } from "zod";

/* Money is signed integer MINOR units, never a float (ARCHITECTURE.md §6). */
export const BudgetSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  category_id: z.number().int(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  period: z.enum(["monthly", "quarterly", "yearly"]),
});

export type Budget = z.infer<typeof BudgetSchema>;
