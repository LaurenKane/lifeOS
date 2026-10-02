/* Review types (stub) */
import { z } from "zod";

/* Money is signed integer MINOR units, never a float (ARCHITECTURE.md §6). */
export const ReviewItemSchema = z.object({
  id: z.string(),
  description: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  status: z.enum(["pending", "posted"]),
});

export type ReviewItem = z.infer<typeof ReviewItemSchema>;
