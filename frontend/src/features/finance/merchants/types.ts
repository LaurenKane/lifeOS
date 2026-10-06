/* ── Merchants and merchant aliases ─────────────────────────────────────────
 * Hand-maintained contract for the curation surface:
 *
 *   GET    /v1/merchants                  → Merchant[]
 *   POST   /v1/merchants                  ← MerchantCreateRequest
 *   PATCH  /v1/merchants/{id}             ← MerchantUpdateRequest
 *   DELETE /v1/merchants/{id}
 *   GET    /v1/merchant-aliases           → MerchantAlias[]
 *   POST   /v1/merchant-aliases           ← MerchantAliasCreateRequest
 *   PATCH  /v1/merchant-aliases/{id}      ← MerchantAliasUpdateRequest
 *   DELETE /v1/merchant-aliases/{id}
 *
 * There is no generated client (ARCHITECTURE.md §7), so this file and
 * `backend/finance/api/routes/merchants.py` are two halves of one contract.
 * Wire names are the server's snake_case throughout, exactly as
 * `features/finance/categories/types.ts` keeps them: a converter would be a
 * second place for a field name to be wrong.
 *
 * WHAT THESE TWO TABLES ARE FOR, because the names alone do not say it.
 * The categorizer tries seven layers in order
 * (`backend/finance/domain/services/categorize.py`). Two of them read rows
 * this contract describes:
 *
 *   layer 2 — a `merchant_alias` raw string, matched as a substring.
 *   layer 3 — a `merchant` name, matched as a substring.
 *   layer 4 — the same merchants, matched fuzzily above 0.60.
 *
 * `finance.ingestion.rules.load_aliases` and `.load_known_merchants` are the
 * two projections, and both SKIP a row with no `category_id`. So a merchant
 * or alias on this screen with a null category is stored and read back
 * faithfully, and feeds nothing at all. That is stated on the screen rather
 * than left to be discovered, because a name a user has carefully typed and
 * then found to do nothing is the most likely way this screen loses trust.
 *
 * FOUR THINGS THE SCREEN HAS TO LIVE WITH, recorded here because none of them
 * shows up in the happy path:
 *
 * 1. `category_id` IS NULLABLE ON BOTH. "Known but unfiled" is a real state on
 *    a merchant and a real state on an alias, and the schema keeps null rather
 *    than defaulting it to 0 or a sentinel category.
 *
 * 2. PATCH IS NOT PUT. `MerchantUpdateRequest` and
 *    `MerchantAliasUpdateRequest` are read with `model_fields_set` server-side:
 *    an absent field means "leave this alone" while an explicit `null` CLEARS
 *    it. So clearing a merchant's category is `{"category_id": null}`, not an
 *    empty body — `apiPatch` is called for exactly that reason, and the client
 *    must send the key even when its value is null.
 *
 * 3. `confidence` IS A `Decimal`, WHICH pydantic SERIALISES AS A JSON STRING.
 *    Kept as a string for the same reason `CategoryRuleSchema` keeps it: this
 *    project does not put floats next to money-adjacent values anywhere. A
 *    number is accepted too, because a stub or a future serializer that sends
 *    `1.0` must not take the whole alias list down over one field. Requests
 *    SEND a string as well, so the value that reaches the `NUMERIC(3,2)`
 *    column is the two decimal places the column can hold and never a float.
 *
 * 4. AN ALIAS'S RAW STRING IS IMMUTABLE. `MerchantAliasUpdateRequest` has no
 *    `raw_string` field at all — renaming the match key is a delete plus a
 *    create, not an edit — and the request models carry `extra="forbid"`, so a
 *    client that tried to send one would get a 422. The screen therefore offers
 *    no rename, and saying so is cheaper than offering a control that 422s. */
import { z } from "zod";

/** One canonical merchant.
 *
 * `category_id: null` means known-but-unfiled, and `finance.ingestion.rules`
 * skips such a row: it feeds neither layer 3 nor layer 4. */
export const MerchantSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  category_id: z.number().int().nullable(),
});
export type Merchant = z.infer<typeof MerchantSchema>;

/** Every merchant, in `name` order. Unpaginated on purpose: the curated set is
 * small by construction and the screen edits it whole. */
export const MerchantListSchema = z.array(MerchantSchema);

/** The body of `POST /merchants`.
 *
 * `name` is 1-500 characters; a blank one is a 422 rather than a row the
 * substring match can never use. `category_id` is optional and absent means
 * unfiled — the same outcome as an explicit null, on a create where there is no
 * stored value to preserve. */
export const MerchantCreateRequestSchema = z.object({
  name: z.string().min(1).max(500),
  category_id: z.number().int().min(1).nullable().optional(),
});
export type MerchantCreateRequest = z.infer<typeof MerchantCreateRequestSchema>;

/** The body of `PATCH /merchants/{id}`.
 *
 * Both fields optional and the difference between an absent key and a null
 * value is load-bearing: absent means "leave it alone", null CLEARS the
 * category. `name` is here because the endpoint renames; this screen sends
 * only `category_id`, since a rename is not part of what it curates. */
export const MerchantUpdateRequestSchema = z.object({
  name: z.string().min(1).max(500).nullable().optional(),
  category_id: z.number().int().min(1).nullable().optional(),
});
export type MerchantUpdateRequest = z.infer<typeof MerchantUpdateRequestSchema>;

/** A `NUMERIC(3,2)` on a `Decimal` column, normalised to a string. See the
 * header, point 3. A number is accepted so a `1.0` from any serializer does
 * not take the whole list down. */
const ConfidenceSchema = z
  .union([z.string(), z.number()])
  .transform((value) => String(value));

/** One stored alias: a raw payee string, and what it means.
 *
 * `merchant_id` and `category_id` are independently nullable, and the server
 * refuses a CREATE with neither — an alias pointing at nothing matches
 * nothing, so storing it would be filing a question instead of an answer. A
 * create that names only a merchant INHERITS that merchant's category, which
 * is why an alias can arrive here filed at a category the form never chose. */
export const MerchantAliasSchema = z.object({
  id: z.number().int(),
  raw_string: z.string(),
  merchant_id: z.number().int().nullable(),
  category_id: z.number().int().nullable(),
  confidence: ConfidenceSchema,
});
export type MerchantAlias = z.infer<typeof MerchantAliasSchema>;

/** Every alias, in `raw_string` order. Unpaginated, like the merchant list. */
export const MerchantAliasListSchema = z.array(MerchantAliasSchema);

/** The body of `POST /merchant-aliases`.
 *
 * At least one of the two targets must be present — the server enforces it
 * with a 422 because "at least one of two" is a constraint neither field
 * carries alone, and the screen gates the submit on the same condition so the
 * refusal is rare rather than routine.
 *
 * `confidence` is a STRING of two decimals, sent that way so no float reaches
 * the decimal column. */
export const MerchantAliasCreateRequestSchema = z.object({
  raw_string: z.string().min(1).max(500),
  category_id: z.number().int().min(1).nullable().optional(),
  merchant_id: z.number().int().min(1).nullable().optional(),
  confidence: z.string().optional(),
});
export type MerchantAliasCreateRequest = z.infer<
  typeof MerchantAliasCreateRequestSchema
>;

/** The body of `PATCH /merchant-aliases/{id}`.
 *
 * Recorded for completeness. This screen does not send it: an alias row is
 * listed and deleted here, and re-pointing one is not part of the curation it
 * performs. `raw_string` is absent from the server's model entirely — it is the
 * match key, and renaming it is a delete plus a create. */
export const MerchantAliasUpdateRequestSchema = z.object({
  category_id: z.number().int().min(1).nullable().optional(),
  merchant_id: z.number().int().min(1).nullable().optional(),
  confidence: z.string().nullable().optional(),
});
export type MerchantAliasUpdateRequest = z.infer<
  typeof MerchantAliasUpdateRequestSchema
>;

/**
 * What a typed confidence means, as the exact decimal string the column takes.
 *
 * `null` when the text is not a number from 0 to 1 — including the empty
 * string, which `Number("")` would happily read as 0 and silently store as an
 * alias that never files anything.
 */
export const normaliseConfidence = (typed: string): string | null => {
  const trimmed = typed.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    return null;
  }
  /* Two decimals, because the column is `NUMERIC(3,2)`. Anything finer is
   * rounded here rather than by the database, so what the screen reported and
   * what the server stored are the same number. */
  return value.toFixed(2);
};

/**
 * The confidence at which the engine files a match without asking.
 *
 * `CategorizeResult.is_auto` is `confidence >= Decimal("0.90")`, and a match
 * below it is advisory. Layer 2 takes its confidence from the alias row itself,
 * so this number is a per-alias decision rather than a constant: an alias
 * written at 0.95 files a matching transaction, the same alias written at 0.80
 * sends it to review.
 */
export const AUTO_BAR = "0.90";

/** The confidence a curated alias is created at, matching the server's default
 * (`Decimal("1.00")`). Stated here so the form's prefill is this build's
 * decision made visible rather than a number that appeared from nowhere. */
export const DEFAULT_ALIAS_CONFIDENCE = "1.00";
