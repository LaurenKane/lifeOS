/* Import types (stub) */
import { z, type ZodType } from "zod";

export const importBatchSchema: ZodType = z.object({
  id: z.string(),
  provider: z.enum(["enable_banking", "amex_csv", "amex_pdf", "revolut_csv", "manual"]),
  status: z.enum(["pending", "processing", "completed", "failed"]),
  sourceFilename: z.string(),
});

export type ImportBatch = z.infer<typeof importBatchSchema>;