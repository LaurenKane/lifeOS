/* ── Accounts ──────────────────────────────────────────────────────────────
 * Hand-maintained contract for `GET/POST /accounts`, `GET /accounts/types`
 * and `GET /accounts/natures`. There is no generated client
 * (`scripts/generate_types.py` emits no real types), so these schemas and the
 * backend's `finance.public.AccountType` / `AccountNature` / `AccountSummary`
 * are two halves of one contract and have to be kept in step by hand.
 *
 * Ids are `int`: the tables hand them out as BIGSERIAL. `currency` is the
 * account's own ISO code, stripped of the CHAR(3) blank padding PostgreSQL
 * stores. */
import { z } from "zod";

/** Mirrors `finance.public.AccountType`. Closed on both sides: an unlisted
 * value fails here rather than rendering as an unlabelled row. */
export const AccountTypeSchema = z.enum([
  "checking",
  "savings",
  "credit_card",
  "cash",
  "investment",
  "loan",
  "mortgage",
]);
export type AccountType = z.infer<typeof AccountTypeSchema>;

/** Mirrors `finance.public.AccountNature`. This is the field that decides which
 * way an amount runs, which is why it is never defaulted. */
export const AccountNatureSchema = z.enum(["asset", "liability", "equity"]);
export type AccountNature = z.infer<typeof AccountNatureSchema>;

export const AccountSummarySchema = z.object({
  id: z.number().int(),
  name: z.string(),
  currency: z.string().length(3),
  account_type: AccountTypeSchema,
  account_nature: AccountNatureSchema,
  is_active: z.boolean(),
  is_hidden: z.boolean(),
  sort_order: z.number().int(),
});
export type AccountSummary = z.infer<typeof AccountSummarySchema>;

/** The body of `POST /accounts`.
 *
 * No `id`: the database hands it out, and a client-chosen primary key is a
 * client-chosen collision. `sort_order`, `is_active` and `is_hidden` have
 * server defaults and the form does not need them. */
export const AccountCreateRequestSchema = z.object({
  name: z.string().min(1).max(200),
  account_type: AccountTypeSchema,
  account_nature: AccountNatureSchema,
  currency: z
    .string()
    .trim()
    .regex(/^[A-Z]{3}$/, "Use a three-letter code such as EUR."),
});
export type AccountCreateRequest = z.infer<typeof AccountCreateRequestSchema>;

export const AccountListSchema = z.array(AccountSummarySchema);
export const AccountTypesSchema = z.array(AccountTypeSchema);
export const AccountNaturesSchema = z.array(AccountNatureSchema);
