/* Import types (stub) */
import { z } from "zod";

/* An ImportBatch describes the provenance and state of one import run.
 * It carries no money field by design: the batch's transactions are
 * typed by the transactions feature, where amounts are signed integer
 * MINOR units (ARCHITECTURE.md §6). */
export const ImportBatchSchema = z.object({
  id: z.string(),
  provider: z.enum(["enable_banking", "amex_csv", "amex_pdf", "revolut_csv", "manual"]),
  status: z.enum(["pending", "processing", "completed", "failed"]),
  sourceFilename: z.string(),
});

export type ImportBatch = z.infer<typeof ImportBatchSchema>;
