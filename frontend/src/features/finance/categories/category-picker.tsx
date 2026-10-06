/* Category picker — the one control that names a category.
 *
 * THE TEXTBOX THIS REPLACES WAS NOT A SMALL PROBLEM.
 * `transactions/detail.tsx` asked for a category as a raw ledger id in a
 * monospace box. That is an internal identifier presented as if it were an
 * answer: a user standing at a transaction cannot know that 12 is "Groceries"
 * without going to look it up somewhere this app never offered. So the picker
 * exists, and it is a shared component rather than a second `<select>` because
 * three surfaces need the same list and one of them — the rule form — needs the
 * same groups in the same order.
 *
 * GROUPED BY KIND, ALWAYS.
 * A flat list of names is a real hazard on this screen. "Savings" could be an
 * investment or a transfer, and those two behave differently in every total the
 * product computes, because `kind` is what decides a balance's sign. The
 * `<optgroup>` makes that visible at the moment of choosing rather than leaving
 * it to be discovered later in a spending breakdown.
 *
 * A NESTED CATEGORY SAYS WHICH BRANCH IT IS IN.
 * `GET /categories` returns `parent_id`, so a control that cannot nest — a native
 * `<select>` — has to say it in words. Two categories can share a name in two
 * branches, and "Coffee" twice with nothing to tell them apart is a choice the
 * user cannot make on purpose. The path is one level deep rather than a full
 * breadcrumb: the options are already grouped by kind, and a path three
 * segments long does not fit the width of a native select on a phone.
 *
 * SYSTEM CATEGORIES ARE IN THE LIST AND ARE MARKED.
 * They are the ones a transaction can be filed under, so omitting them would
 * make the picker wrong. `is_system` is stated next to the name — in words, not
 * by a disabled option, because a disabled option is a control the user cannot
 * use and it does not explain why.
 *
 * THE CONTROL IS A NATIVE `<select>` AND STAYS ONE.
 * A combobox with filtering, keyboard handling and a popup list is a component
 * library, and this app has none installed. A native select is keyboard
 * operable, screen-reader labelled and mobile-correct for free, and it degrades
 * to the platform's own control on a machine whose fonts this design does not
 * control — the same argument that sends the disclosure triangle to `icons.tsx`. */
import React from "react";
import {
  buildCategoryTree,
  flattenTree,
  groupByKind,
  parentNameOf,
  useCategoriesContext,
} from "./use-categories";
import { KIND_LABEL, KIND_ORDER } from "./types";
import type { Category } from "./types";

/** The picker itself. `id` is required rather than generated, because every
 * caller wraps this in a `Field` that needs the same id for its `<label>`; a
 * generated id would break that association the moment the two disagreed. */
export const CategoryPicker: React.FC<{
  value: string;
  onChange: (value: string) => void;
  id: string;
  disabled?: boolean;
  /** What the empty option says. Copy varies by caller because the reason the
   * user is on this screen varies, and a generic "Choose…" throws away the only
   * chance to say why the field is here. */
  placeholder: string;
  /** Show each category's id beside its name. On by default: the id is what
   * every other screen and the delete endpoint agree on, so a user comparing
   * this list with a rule needs it. */
  showIds?: boolean;
  /** Overrides the context list. Only used by tests that mount this control
   * without a provider; production callers take the shared one. */
  categories?: readonly Category[];
}> = ({
  value,
  onChange,
  id,
  disabled = false,
  placeholder,
  showIds = true,
  categories: provided,
}) => {
  const { categories, categoriesLoading, categoriesError, kinds } =
    useCategoriesContext();

  const list = provided ?? categories;
  /* `kinds` is null when the server's kind list could not be read, which is not
     a reason to reorder anything: `KIND_ORDER` is the order this build knows
     and it covers every kind the schema accepts. */
  const groups = groupByKind(list, kinds ?? KIND_ORDER);

  return (
    <select
      id={id}
      className="field"
      value={value}
      /* A picker whose categories could not be read is disabled rather than
         empty: offering a choice list that silently lacks categories would let a
         user file a transaction somewhere they were not offered. */
      disabled={disabled || categoriesLoading || categoriesError !== null}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">
        {categoriesLoading
          ? "Reading categories…"
          : categoriesError !== null
            ? "Categories could not be read"
            : placeholder}
      </option>
      {groups.length === 0 && !categoriesLoading && categoriesError === null && (
        /* An empty select carrying only a placeholder is a control offering
           nothing. Saying why is the honest version. */
        <option value="" disabled>
          No categories exist yet
        </option>
      )}
      {groups.map((group) => (
        <optgroup key={group.kind} label={KIND_LABEL[group.kind]}>
          {/* Tree order, flattened: a nested category follows the parent it sits
              under, which is the order a reader scanning for it expects. */}
          {flattenTree(buildCategoryTree(group.categories, list)).map((category) => {
            const branch = parentNameOf(category, list);
            return (
              <option key={category.id} value={category.id}>
                {showIds ? `${category.name} · ${category.id}` : category.name}
                {branch === null ? "" : ` — under ${branch}`}
                {category.is_system ? " · system" : ""}
              </option>
            );
          })}
        </optgroup>
      ))}
    </select>
  );
};