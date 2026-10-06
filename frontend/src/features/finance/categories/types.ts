/* ── Categories and categorization rules ─────────────────────────────────────
 * Hand-maintained contract for the categorization surface:
 *
 *   GET    /v1/categories?kind=<kind>   → Category[]
 *   POST   /v1/categories               ← CategoryCreateRequest
 *   GET    /v1/categories/kinds         → CategoryKind[]
 *   GET    /v1/categories/rules         → CategoryRule[]
 *   POST   /v1/categories/rules         ← CategoryRuleCreateRequest
 *   DELETE /v1/categories/rules/{rule_id}
 *
 * There is no generated client (ARCHITECTURE.md §7), so this file and
 * `backend/finance/api/routes/categories.py` are two halves of one contract.
 * Wire names are the server's snake_case throughout, exactly as
 * `features/finance/transactions/types.ts` keeps them: a converter would be a
 * second place for a field name to be wrong.
 *
 * THREE THINGS THE SCREENS HAVE TO LIVE WITH, recorded here because none of
 * them is visible in the happy path:
 *
 * 1. `CategorySummary` CARRIES `parent_id`, AND A CHILD'S KIND MAY DIFFER FROM
 *    ITS PARENT'S. The server checks that a parent exists and that
 *    `(parent_id, name)` is unique; it does not check that the two share a
 *    kind, so a parent in one kind can hold a child in another. `kind` is what
 *    the ledger reports on and what decides a balance's sign, so it is the RANK
 *    of the list and `parent_id` is the nesting inside it — never the other way
 *    round. `buildTree` returns the shape; `page.tsx` decides where it is
 *    drawn, and the row that cannot sit under its parent says so.
 *
 * 2. `CategoryRuleSummary.description_pattern` is NULLABLE, and the delete is
 *    BY `id`. The two facts used to be connected: the delete used to take a
 *    pattern as one path segment, so a rule whose pattern held a slash — or no
 *    pattern at all — was on screen and not addressable, and the screen said so
 *    instead of offering a button that could not work. An integer id has one
 *    representation, so every rule in the list can be addressed and the row
 *    carries no such apology. The pattern is still nullable, and a rule without
 *    one is still rendered as such: it is matched on its account or merchant
 *    criterion, which is a fact about the rule rather than about the screen.
 *
 * 3. `confidence` is a `NUMERIC(3,2)` on a `Decimal` column, which pydantic
 *    serialises as a JSON STRING. It is normalised to a string here and accepts a
 *    number too, because a stub or a future serializer that sends `1.0` must
 *    not take the whole rule list down over one field.
 *
 * `priority` is the engine's order and it is NOT an enum: it is a plain INT so
 * that a new tier can be slotted in above the defaults without rewriting every
 * stored rule (see `taxonomy.CategoryRule`). Lower is matched first. */
import { z } from "zod";

/** Mirrors `finance.public.CategoryKind` — the closed set. `kind` is what the
 * ledger asks about: an `expense` category reduces a balance, an `income` one
 * raises it, a `transfer` one is neither, and an `investment` one is the
 * groundwork M9/M10 will report on. */
export const CategoryKindSchema = z.enum([
  "expense",
  "income",
  "transfer",
  "investment",
]);
export type CategoryKind = z.infer<typeof CategoryKindSchema>;

/** `GET /categories/kinds` answers the enum itself. Refused rather than
 * defaulted: a fourth kind would be a decision this build cannot phrase, so it
 * fails here instead of rendering as an unlabelled group. */
export const CategoryKindListSchema = z.array(CategoryKindSchema);

/** One category. `is_system` marks the seeded rows, which this build can read
 * and file transactions under but not rename or remove — there is no endpoint
 * for either.
 *
 * `parent_id` is the edge the tree is drawn from, and it is NULLABLE because a
 * top-level category has no parent. It is defaulted rather than required: the
 * server sends it on every row, and a payload from before it existed reads as a
 * flat list — every category top-level, which is what such a payload says —
 * instead of failing the whole read over a missing field. Refusing it outright
 * would take the list down for a reason the user cannot do anything about. */
export const CategorySchema = z.object({
  id: z.number().int(),
  name: z.string(),
  kind: CategoryKindSchema,
  is_system: z.boolean().default(false),
  parent_id: z.number().int().min(1).nullable().default(null),
});
export type Category = z.infer<typeof CategorySchema>;

export const CategoryListSchema = z.array(CategorySchema);

/** The body of `POST /categories`.
 *
 * No `id`: the database hands it out, and `is_system` is absent on purpose —
 * only seeded rows carry it, and a request must not be able to mint one. An
 * absent `parent_id` means a top-level category. */
export const CategoryCreateRequestSchema = z.object({
  name: z.string().min(1).max(200),
  kind: CategoryKindSchema,
  parent_id: z.number().int().min(1).nullable().optional(),
});
export type CategoryCreateRequest = z.infer<typeof CategoryCreateRequestSchema>;

/** The engine's own number, kept as a string for one reason: `Decimal`.
 *
 * A JSON number here is a float, and this project does not put floats next to
 * money-adjacent values anywhere. Accepting one anyway costs a union and keeps
 * a `1.0` from failing a whole rule list. */
const ConfidenceSchema = z
  .union([z.string(), z.number()])
  .transform((value) => String(value));

/** One stored rule, hand or learned, as the rules screen reads it.
 *
 * `description_pattern` is NULLABLE and the schema keeps it that way — see the
 * header. `is_learned` is the distinction the screen is built around: a hand
 * rule is a decision, a learned one is the system remembering a decision.
 *
 * `confidence` is the matcher's own score, not an enum. It is shown on learned
 * rules only, where it carries information; a hand rule has no score because it
 * does not need one. */
export const CategoryRuleSchema = z.object({
  id: z.number().int(),
  description_pattern: z.string().nullable(),
  priority: z.number().int(),
  category_id: z.number().int(),
  is_learned: z.boolean(),
  confidence: ConfidenceSchema,
});
export type CategoryRule = z.infer<typeof CategoryRuleSchema>;

/** Every rule, hand and learned, in engine order (`priority`, then `id`).
 *
 * Unpaginated by design, backend-side and here: section I requires the entire
 * rule set to be readable on one screen, and a rule the user cannot see is a
 * rule they will not fix. */
export const CategoryRuleListSchema = z.array(CategoryRuleSchema);

/** The body of `POST /categories/rules`.
 *
 * `description_pattern` is a PLAIN SUBSTRING — not a regular expression, not a
 * DSL. The server strips surrounding whitespace and rejects an empty one with a
 * 422, which is the right refusal: a blank pattern would match everything.
 * `priority` defaults to 100, the hand-rule tier, which outranks every learned
 * rule at 500. */
export const CategoryRuleCreateRequestSchema = z.object({
  description_pattern: z.string().min(1),
  category_id: z.number().int().min(1),
  priority: z.number().int().default(100),
});
export type CategoryRuleCreateRequest = z.infer<typeof CategoryRuleCreateRequestSchema>;

/** What `DELETE /categories/rules/{rule_id}` answers.
 *
 * `id` is echoed back rather than assumed, because the screen removes exactly
 * the row it asked about and this is the server naming it: the delete takes one
 * integer and removes ONE rule, so there is no pattern in the answer to quote
 * and no count to report. */
export const RuleDeleteResultSchema = z.object({
  status: z.string(),
  id: z.number().int(),
});
export type RuleDeleteResult = z.infer<typeof RuleDeleteResultSchema>;

/** The four kinds in the order they are shown, with the word each is read as.
 *
 * Ledger order rather than alphabetical: what came out, what came in, what moved
 * between your own accounts, what you hold. A group a reader scans for first
 * should be first. */
export const KIND_ORDER: ReadonlyArray<CategoryKind> = [
  "expense",
  "income",
  "transfer",
  "investment",
];

export const KIND_LABEL: Record<CategoryKind, string> = {
  expense: "Expense",
  income: "Income",
  transfer: "Transfer",
  investment: "Investment",
};

/** What each kind does, said once, in the place a reader needs it. */
export const KIND_NOTE: Record<CategoryKind, string> = {
  expense: "Money out. A transaction in this category reduces the balance.",
  income: "Money in. Raises the balance.",
  transfer: "Money moved between your own accounts. Not spending, and not income.",
  investment: "Money set aside to hold. Not spending, and not income either.",
};