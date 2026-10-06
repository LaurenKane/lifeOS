/* Curate a name — create a merchant or a raw-string alias.
 *
 * TWO FORMS IN ONE PANEL, because the two are two halves of one filing decision
 * and the honest order is: teach the engine a name, then teach it what a bank's
 * version of that name looks like. A user arriving from a review-queue item
 * holding "PAYPAL XYZ 4588" needs both, and splitting them across two pages
 * would make the second one a place they have to find.
 *
 * BOTH FORMS SHARE THE MERCHANT LIST, which is why they live together: the alias
 * form can file either at a category or at a merchant, and the merchant names
 * are already in scope. `confidence` appears only on the alias, because a
 * merchant has no score — it contributes at the engine's own fixed layer-3
 * confidence whenever it has a category, and printing a number next to it would
 * be inventing one.
 *
 * AN ALIAS NEEDS A TARGET, AND THE GATE SAYS WHICH. The server refuses a create
 * with neither `category_id` nor `merchant_id` (422, "An alias needs a
 * category_id, a merchant_id, or both"), and that is the right refusal: a string
 * pointing at nothing is a question stored as an answer. The submit is gated on
 * the same condition rather than left to fail, and the hint under the category
 * field states the inheritance rule — name only a merchant and the alias takes
 * that merchant's category — because that is what makes filing both in one step
 * work, and it is not otherwise visible.
 *
 * A REFUSAL IS THE SERVER'S OWN WORDS. A 409 for a duplicate name or raw
 * string, and a 422 for a blank one, say different things and both are more
 * useful than anything these forms could invent. */
import React from "react";
import { CategoryPicker } from "@/features/finance/categories/category-picker";
import { Field, Notice, Panel } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { useCategoriesContext } from "@/features/finance/categories/use-categories";
import { byText, useMerchantsContext } from "./use-merchants";
import type { Merchant } from "./types";
import { DEFAULT_ALIAS_CONFIDENCE, normaliseConfidence } from "./types";

export const CuratePanel: React.FC<{
  onSaved: (message: string) => void;
}> = ({ onSaved }) => (
  <div className="space-y-6">
    <NewMerchantForm onSaved={onSaved} />
    <NewAliasForm onSaved={onSaved} />
  </div>
);

/* ── A merchant ────────────────────────────────────────────────────────────── */

const NewMerchantForm: React.FC<{ onSaved: (message: string) => void }> = ({ onSaved }) => {
  const { categories, categoriesLoading, categoriesError } = useCategoriesContext();
  const { merchants, createMerchant } = useMerchantsContext();

  const [name, setName] = React.useState("");
  const [categoryId, setCategoryId] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);

  const trimmed = name.trim();
  /* The name duplicates something already stored, which the server will refuse
   * with a 409. Caught here so the button does not offer a request the ledger
   * has already said it will not accept — and so the reason is given before the
   * click rather than after it. Matching is case-insensitive because the
   * uniqueness constraint is, and `merchant` keys the matcher's map on the
   * lowercased name. */
  const duplicate =
    trimmed !== "" && merchants.some((merchant) => byText(merchant.name, trimmed) === 0)
      ? "A merchant with this name is already stored."
      : null;

  /* A fact about the DATA closes the fields (no categories means nothing to file
   * under); an incomplete form gates only the button. Disabling the name box
   * because it is empty would be a form with no way out. */
  const closed = categoriesLoading || categoriesError !== null;
  const disabled = closed || saving;
  const canSubmit = !closed && !saving && trimmed !== "" && duplicate === null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (trimmed === "" || duplicate !== null) {
      return;
    }
    setSaving(true);
    setRefused(null);
    setSaved(null);
    try {
      /* `null` when no category was chosen, not an absent key: on a create the
       * two are equivalent server-side, and sending the key means the body reads
       * the same as the one the API documents. */
      const chosen = categoryId === "" ? null : Number(categoryId);
      const created = await createMerchant({ name: trimmed, category_id: chosen });
      const where = chosen === null ? "It matches nothing until it is filed." : "It starts matching now.";
      setSaved(
        `${created.name} is stored as merchant ${created.id}. ${where}`,
      );
      onSaved(`${created.name} was added to the merchants.`);
      setName("");
      setCategoryId("");
    } catch (cause) {
      setRefused(describeError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel title="Add a merchant" description="A name the ledger should recognise.">
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {categoriesError !== null && (
          /* Says something the page-level notice does not: this explains why the
             controls below are closed. Repeating the server's message in both
             places would be two red boxes saying one thing. */
          <Notice tone="warning" label="Filing is closed">
            The category list could not be read, so there is nothing to file a new
            merchant under. It can still be saved, but a merchant with no category
            matches nothing.
          </Notice>
        )}

        <Field
          label="Merchant name"
          htmlFor="merchant-name"
          error={duplicate}
          hint="The name as you would write it. Up to 500 characters."
        >
          <input
            id="merchant-name"
            className="field font-mono"
            value={name}
            maxLength={500}
            disabled={disabled}
            placeholder="Albert Heijn"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        <Field
          label="Category"
          htmlFor="merchant-category"
          hint={
            categoryId === ""
              ? "Optional. A merchant with no category is remembered and matches nothing."
              : `Filed as ${categories.find((c) => `${c.id}` === categoryId)?.name ?? "the chosen category"}.`
          }
        >
          <CategoryPicker
            id="merchant-category"
            value={categoryId}
            onChange={setCategoryId}
            placeholder="Save it unfiled"
            disabled={disabled}
          />
        </Field>

        {saved !== null && <Notice tone="info" label="Added">{saved}</Notice>}
        {refused !== null && (
          <Notice tone="error" label="The merchant was not added">
            {refused}
          </Notice>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={!canSubmit}>
          {saving ? "Saving…" : "Add the merchant"}
        </button>
      </form>
    </Panel>
  );
};

/* ── An alias ──────────────────────────────────────────────────────────────── */

const NewAliasForm: React.FC<{ onSaved: (message: string) => void }> = ({ onSaved }) => {
  const { categories, categoriesLoading, categoriesError } = useCategoriesContext();
  const { merchants, aliases, merchantsError, createAlias } = useMerchantsContext();

  const [rawString, setRawString] = React.useState("");
  const [categoryId, setCategoryId] = React.useState("");
  const [merchantId, setMerchantId] = React.useState("");
  const [confidence, setConfidence] = React.useState(DEFAULT_ALIAS_CONFIDENCE);
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);

  const trimmed = rawString.trim();
  const duplicate =
    trimmed !== "" && aliases.some((alias) => byText(alias.raw_string, trimmed) === 0)
      ? "An alias with this text is already stored."
      : null;

  const chosenMerchant: Merchant | null =
    merchants.find((merchant) => `${merchant.id}` === merchantId) ?? null;

  /* The confidence field is optional in the sense that its default is right, not
   * in the sense that anything goes: an unparseable score would be refused by
   * the column, so it is caught where it is typed. */
  const score = normaliseConfidence(confidence);
  const scoreError =
    score === null ? "A score from 0 to 1. 1.00 files a match; 0.90 is the bar." : null;

  /* At least one target, which is what the server requires. */
  const targeted = categoryId !== "" || merchantId !== "";
  /* Merchants could not be read, so the merchant half of the target is closed
   * even though the category half is open — a fact about one list, not both. */
  const closed = categoriesLoading || categoriesError !== null;
  const disabled = closed || saving;
  const canSubmit =
    !closed && !saving && trimmed !== "" && duplicate === null && targeted && score !== null;

  /* What the created alias will actually mean, stated before it exists. This is
   * the inheritance rule made visible: naming only a merchant copies that
   * merchant's category, and a user who does not know that would see a category
   * appear they never picked. */
  const outcome = (() => {
    if (categoryId !== "") {
      return `It will file matching transactions as ${
        categories.find((c) => `${c.id}` === categoryId)?.name ?? "the chosen category"
      }.`;
    }
    if (chosenMerchant !== null) {
      return chosenMerchant.category_id === null
        ? `${chosenMerchant.name} has no category, so this alias would match nothing until one of them is filed.`
        : `${chosenMerchant.name} is filed, so this alias inherits that category.`;
    }
    return null;
  })();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (trimmed === "" || !targeted || score === null) {
      return;
    }
    setSaving(true);
    setRefused(null);
    setSaved(null);
    try {
      const created = await createAlias({
        raw_string: trimmed,
        category_id: categoryId === "" ? null : Number(categoryId),
        merchant_id: merchantId === "" ? null : Number(merchantId),
        /* A STRING, not a float: the column is `NUMERIC(3,2)` and this project
           does not put floats next to money-adjacent values. `normaliseConfidence`
           has already fixed it to two decimals, which is the column's precision. */
        confidence: score,
      });
      setSaved(
        `“${created.raw_string}” is stored as alias ${created.id} at confidence ${created.confidence}.`,
      );
      onSaved(`The alias “${created.raw_string}” was added.`);
      setRawString("");
      setCategoryId("");
      setMerchantId("");
      setConfidence(DEFAULT_ALIAS_CONFIDENCE);
    } catch (cause) {
      setRefused(describeError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      title="Add an alias"
      description="A string a bank sent, and what it means."
    >
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {categoriesError !== null && (
          <Notice tone="warning" label="Filing is closed">
            The category list could not be read, so an alias can only be filed at a
            merchant, and only if that merchant already has a category.
          </Notice>
        )}

        <Field
          label="Raw string"
          htmlFor="alias-raw"
          error={duplicate}
          hint="Matched as plain text anywhere in the description. Up to 500 characters."
        >
          <input
            id="alias-raw"
            className="field font-mono"
            value={rawString}
            maxLength={500}
            disabled={disabled}
            placeholder="ALBERTHEIJN 1234 AMSTERDAM"
            onChange={(event) => setRawString(event.target.value)}
          />
        </Field>

        <Field
          label="Category"
          htmlFor="alias-category"
          hint={
            categoryId === "" && chosenMerchant !== null
              ? "Leave empty to inherit the merchant's category."
              : "Where a matching transaction is filed. Needs a category or a merchant."
          }
        >
          <CategoryPicker
            id="alias-category"
            value={categoryId}
            onChange={setCategoryId}
            placeholder="Use the merchant's category"
            disabled={disabled}
          />
        </Field>

        <Field
          label="Merchant"
          htmlFor="alias-merchant"
          hint={
            merchantsError !== null
              ? "The merchant list could not be read, so this field is empty."
              : merchantId === ""
                ? "Optional. Naming a merchant records which name this string stands for."
                : `Records that this string stands for ${
                    chosenMerchant?.name ?? "the chosen merchant"
                  }.`
          }
        >
          <select
            id="alias-merchant"
            className="field"
            value={merchantId}
            disabled={disabled || merchantsError !== null}
            onChange={(event) => setMerchantId(event.target.value)}
          >
            <option value="">
              {merchantsError !== null
                ? "Merchants could not be read"
                : merchants.length === 0
                  ? "No merchants stored yet"
                  : "Not tied to a merchant"}
            </option>
            {[...merchants]
              .sort((a, b) => byText(a.name, b.name))
              .map((merchant) => (
                <option key={merchant.id} value={merchant.id}>
                  {merchant.name}
                  {merchant.category_id === null ? " · no category" : ""}
                </option>
              ))}
          </select>
        </Field>

        <Field
          label="Confidence"
          htmlFor="alias-confidence"
          error={scoreError}
          hint="How sure this mapping is. 0.90 and above files a match without asking."
        >
          <input
            id="alias-confidence"
            className="field font-mono"
            value={confidence}
            disabled={disabled}
            inputMode="decimal"
            placeholder={DEFAULT_ALIAS_CONFIDENCE}
            onChange={(event) => setConfidence(event.target.value)}
          />
        </Field>

        {/* What this alias will do, said before it exists. Stated as words rather
            than left for the reader to work out from two dropdowns. */}
        {targeted && outcome !== null && (
          <p className="text-xs leading-relaxed text-muted-foreground">{outcome}</p>
        )}

        {saved !== null && <Notice tone="info" label="Added">{saved}</Notice>}
        {refused !== null && (
          <Notice tone="error" label="The alias was not added">
            {refused}
          </Notice>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={!canSubmit}>
          {saving ? "Saving…" : "Add the alias"}
        </button>
      </form>
    </Panel>
  );
};
