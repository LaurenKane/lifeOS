/* Import types — the wire contract for the upload form and its answer.
 *
 * Hand-maintained, like every other schema in this app: there is no generated
 * client (ARCHITECTURE.md §7), so these zod schemas and
 * `backend/finance/api/schemas` are two halves of one contract. */
import { z } from "zod";

/* An ImportBatch describes the provenance and state of one import run.
 * It carries no money field by design: the batch's transactions are
 * typed by the transactions feature, where amounts are signed integer
 * MINOR units (ARCHITECTURE.md §6).
 *
 * Provider enum: v1 source of truth is backend/finance/api/routes/imports.py
 * V1_PROVIDERS. See docs/adr/0002-import-provider-enum.md. */
export const ImportBatchSchema = z.object({
  id: z.number().int(),
  provider: z.enum([
    "enable_banking",
    "amex_pdf",
    "rabobank_pdf",
    "revolut_pdf",
    "manual",
  ]),
  status: z.enum(["pending", "processing", "completed", "failed"]),
  sourceFilename: z.string(),
});

export type ImportBatch = z.infer<typeof ImportBatchSchema>;

/** The providers that accept a FILE upload.
 *
 * Three of the five v1 providers, and it is worth saying why the other two are
 * absent rather than leaving them out silently: `enable_banking` arrives over
 * its API and `manual` is typed field by field, so neither is a file. This
 * mirrors `_FILE_ADAPTERS` in `backend/finance/api/routes/imports.py`, which is
 * also what decides the accepted extension per provider.
 *
 * The backend publishes the same table at `GET /imports/providers`, and that is
 * the better source once a client wants to be sure; this list is here so the
 * form is not empty while that request is in flight.
 */
export const FILE_PROVIDERS = ["amex_pdf", "rabobank_pdf", "revolut_pdf"] as const;
export type FileProvider = (typeof FILE_PROVIDERS)[number];

export const FileProviderSchema = z.enum(FILE_PROVIDERS);

/** The Revolut sections an annual statement is split into.
 *
 * Keys are the adapter's own section keys, lowercased — `_SECTION_PRODUCTS` in
 * `backend/finance/ingestion/adapters/revolut_pdf.py`. The backend lowercases
 * whatever key it is given, so `"Account"` and `"account"` name the same section,
 * and an unlisted name is a 400 rather than a silently ignored entry.
 *
 * Only Revolut takes this. Its statement covers several products, which is why
 * one account id per upload is not enough; every other provider reads a
 * single-account file and the mapping is meaningless there.
 */
export const REVOLUT_SECTIONS = ["account", "deposit"] as const;
export type RevolutSection = (typeof REVOLUT_SECTIONS)[number];

/** section key -> the product name the statement prints for it. */
export const REVOLUT_SECTION_LABELS: Record<RevolutSection, string> = {
  account: "Current Account",
  deposit: "Deposit",
};

/** Section -> local account id. Absent sections are simply not in the object,
 * and the adapter leaves those rows unattributed on purpose rather than
 * guessing an account. */
export type SectionAccountIds = Partial<Record<RevolutSection, number>>;

/** The answer from `POST /imports/file` and `POST /imports`.
 *
 * `created` / `duplicated` / `failed` describe what PERSISTED, and
 * `record_count` describes what the parser read — different claims, which is why
 * this schema carries all four. `created + duplicated === record_count` is the
 * question "did everything land"; `status` is the batch's own word for it.
 */
export const ImportSummarySchema = z.object({
  provider: z.string(),
  import_method: z.string(),
  status: z.enum(["pending", "processing", "completed", "partial", "failed"]),
  record_count: z.number().int(),
  source_checksum: z.string().nullable(),
  created: z.number().int(),
  duplicated: z.number().int(),
  failed: z.number().int(),
  failures: z.array(z.string()),
});

export type ImportSummary = z.infer<typeof ImportSummarySchema>;
