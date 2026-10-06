/* Merchant line — one canonical merchant, and the control that files it.
 *
 * THE SAME GRAMMAR AS THE RULE LINE, ON PURPOSE.
 * A merchant row is the same sentence a rule row is: this exact text, meaning
 * this category. Three curation screens that all render that sentence the same
 * way let a user move between them without re-learning the page, and the
 * categories screen's rows are read-only — this one has the one field on this
 * screen a user actually comes to change, so the difference is a control rather
 * than a different visual language.
 *
 *   "ALBERT HEIJN"  →  Groceries
 *
 * THE NAME IS MONO AND ALLOWED TO BREAK ANYWHERE, because it is the exact text
 * the matcher compares: `load_known_merchants` keys on the lowercased name and
 * layer 3 tests that string as a substring, so a clipped or ellipsed name is a
 * name the user cannot check against the statement it came from.
 *
 * AN UNFILED MERCHANT SAYS SO IN WORDS, AND THE WORDS ARE THE POINT.
 * `load_known_merchants` skips any merchant with no `category_id`: the row is
 * stored, read back faithfully, and matches nothing at either layer 3 or layer
 * 4. That is the honest answer for a name nobody filed, but it is also a trap —
 * a user who types a name and sees it appear has no other way to know it is
 * inert. So the row prints the consequence in a sentence instead of leaving an
 * arrow pointing at nothing.
 *
 * THE PICKER IS A CONTROL, NOT A LABEL, AND ITS BLANK OPTION IS THE CLEAR.
 * Choosing the empty option sends `{category_id: null}`, which the server's
 * `model_fields_set` reads as an explicit clear. That is the one control doing
 * both jobs the screen promises, and it is the shared `CategoryPicker` so the
 * grouping here cannot drift from the grouping in the transaction detail page.
 *
 * THE SELECT SNAPS BACK ON A REFUSED WRITE, and it does that for free: the value
 * comes from the stored row and `setMerchantCategory` replaces state only with
 * what the server returned. A write that fails leaves the row as it was, so the
 * control returns to the value the ledger actually holds. */
import React from "react";
import { CategoryPicker } from "@/features/finance/categories/category-picker";
import type { Category } from "@/features/finance/categories/types";

export const MerchantLine: React.FC<{
  merchantId: number;
  name: string;
  categoryId: number | null;
  /** The category the id names, when this build could read it. `undefined`
   * means the id resolved to nothing — a failed category read, or a category
   * the ledger no longer holds. */
  category: Category | undefined;
  /** Every category, for the picker. Passed rather than read from context so the
   * row does not care where the list came from. */
  categories: readonly Category[];
  busy: boolean;
  onCategoryChange: (merchantId: number, categoryId: number | null) => void;
  onDelete: (merchantId: number, name: string) => void;
  style?: React.CSSProperties;
}> = ({
  merchantId,
  name,
  categoryId,
  category,
  categories,
  busy,
  onCategoryChange,
  onDelete,
  style,
}) => {
  const pickerId = `merchant-category-${merchantId}`;
  const filed = categoryId !== null;

  return (
    <li
      className="rise-row grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 px-5 py-4"
      style={style}
    >
      <div className="min-w-0">
        {/* The subject. Same mono, same size, same `break-all` as a rule
            pattern, because it is the same kind of thing: exact matcher input. */}
        <p className="font-mono text-[0.9375rem] leading-snug break-all">{name}</p>

        {/* The sentence: what it means. A filed merchant prints the arrow and the
            category; an unfiled one prints the consequence in words rather than
            an arrow to nowhere. */}
        {filed ? (
          <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-sm">
            <span aria-hidden className="text-muted-foreground">
              →
            </span>
            <span className="font-medium">{category?.name ?? `category ${categoryId}`}</span>
            {/* The id beside the name, as everywhere else: it is what every other
                screen and the endpoint agree on. Omitted rather than printed as
                "id undefined" when the id resolved to nothing, because a blank
                next to a category id reads as a rendering fault. */}
            {category !== undefined && (
              <span className="font-mono text-xs text-muted-foreground">
                id {category.id}
              </span>
            )}
          </p>
        ) : (
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            No category, so this name matches nothing. The engine skips any merchant
            without one — give it a category and it starts matching.
          </p>
        )}
      </div>

      {/* The control and the action, stacked on the right. The picker sits above
          the button so the two occupy one column and the row's height is set by
          the control rather than by the text beside it. */}
      <div className="flex w-44 shrink-0 flex-col items-stretch gap-2">
        <label htmlFor={pickerId} className="sr-only">
          Category for {name}
        </label>
        <CategoryPicker
          id={pickerId}
          value={categoryId === null ? "" : String(categoryId)}
          categories={categories}
          /* The blank option's own words are this screen's clearest statement of
             what clearing does, so they are not the generic "Choose…". */
          placeholder={filed ? "Clear the category" : "File it under…"}
          showIds={false}
          disabled={busy}
          onChange={(value) =>
            /* `""` is the clear. Sent as null, because the key being PRESENT is
               what makes the server clear rather than ignore. */
            onCategoryChange(merchantId, value === "" ? null : Number(value))
          }
        />
        <button
          type="button"
          className="btn btn-quiet px-2.5 py-1.5 text-xs"
          disabled={busy}
          onClick={() => onDelete(merchantId, name)}
          aria-label={`Delete the merchant ${name}`}
        >
          Delete
        </button>
      </div>
    </li>
  );
};
