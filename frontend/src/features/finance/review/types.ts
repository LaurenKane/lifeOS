/* Review types (stub) */
import { z, type ZodType } from "zod";

export const ReviewItemSchema: ZodType = z.object({
  id: z.string(),
  description: z.string(),
  amountMinor: z.number().int(),
  currency: z.string().length(3),
  status: z.enum(["pending", "posted"]),
});

export type ReviewItem = z.infer<typeof ReviewItemSchema>;