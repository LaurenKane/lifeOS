/* Budget types (stub) */
import { z } from "zod";

/* Money is signed integer MINOR units, never a float (ARCHITECTURE.md §6). */
export const BudgetSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  period: z.enum(["monthly", "quarterly", "yearly"]),
});

export type Budget = z.infer<typeof BudgetSchema>;
