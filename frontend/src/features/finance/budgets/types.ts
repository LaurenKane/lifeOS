/* Budget types (stub) */
import { z, type ZodType } from "zod";

export const BudgetSchema: ZodType = z.object({
  id: z.string(),
  name: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  period: z.enum(["monthly", "quarterly", "yearly"]),
});

export type Budget = z.infer<typeof BudgetSchema>;