/* Alias line — one stored raw string, and what it was filed as.
 *
 * THE SAME GRAMMAR AGAIN: this text means this category. What differs is that
 * the subject is a statement fragment rather than a merchant name, so it is set
 * larger and allowed to break anywhere — a bank payee string is the one field on
 * this screen a user pastes in verbatim and has to be able to check character by
 * character against the statement it came from.
 *
 * CONFIDENCE IS SHOWN ON EVERY ALIAS, and it is shown because it decides
 * something. Unlike a rule's `confidence` — printed only when it is not the
 * constant every learned rule carries — an alias's score is a per-row value
 * that changes what the engine does: `CategorizeResult.is_auto` is
 * `confidence >= 0.90`, and layer 2 takes its confidence from the alias row
 * itself. An alias at 1.00 files a matching transaction; the same alias at 0.60
 * files nothing and sends the transaction to review. That is a distinction the
 * user chose, and a screen that hid it would be hiding a decision.
 *
 * THE LINK TO A MERCHANT IS STATED, NOT ASSUMED.
 * `POST /merchant-aliases` lets a create name a merchant with no category, in
 * which case the alias INHERITS that merchant's category. So an alias can
 * arrive carrying a category nobody picked on the form, and it can also be
 * repointed to a different merchant while keeping a category of its own. Both
 * rows exist in the data, and both are printed: what this string currently means
 * is a fact the reader is owed before they delete it.
 *
 * `merchant_id` and `category_id` are independently nullable, and the server
 * refuses a create with neither — so one of the two is always resolvable on a
 * row the server accepted. The row still handles both being null rather than
 * printing an arrow to nowhere, because `MerchantAliasSummary` admits it and a
 * screen that assumed its own writes were the only ones would be wrong. */
import React from "react";
import type { Category } from "@/features/finance/categories/types";
import { AUTO_BAR } from "./types";

/** The bar, as a number, for the comparison the words below are derived from.
 * The schema hands `confidence` back as a string off a `Decimal`, so this parses
 * rather than compares: `"0.9" >= "0.90"` is true as text and false as a
 * number, and an alias sitting exactly on the bar must not be described as
 * above it. `NaN` — which is all a non-numeric string can produce — is treated
 * as not-filed-automatically, because a score that cannot be read is not a score
 * that clears anything. */
const isAuto = (confidence: string): boolean => {
  const value = Number(confidence);
  return Number.isFinite(value) && value >= Number(AUTO_BAR);
};

export const AliasLine: React.FC<{
  aliasId: number;
  rawString: string;
  categoryId: number | null;
  category: Category | undefined;
  merchantId: number | null;
  /** The merchant's name, when this build could read it. */
  merchantName: string | null;
  confidence: string;
  busy: boolean;
  onDelete: (aliasId: number, rawString: string) => void;
  style?: React.CSSProperties;
}> = ({
  aliasId,
  rawString,
  categoryId,
  category,
  merchantId,
  merchantName,
  confidence,
  busy,
  onDelete,
  style,
}) => {
  const automatic = isAuto(confidence);

  return (
    <li
      className="rise-row grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 px-5 py-4"
      style={style}
    >
      <div className="min-w-0">
        {/* The subject. One step above the surrounding text, because it is
            statement text the matcher compares as a substring. */}
        <p className="font-mono text-[0.9375rem] leading-snug break-all">
          {rawString}
        </p>

        {categoryId !== null ? (
          <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-sm">
            <span aria-hidden className="text-muted-foreground">
              →
            </span>
            <span className="font-medium">{category?.name ?? `category ${categoryId}`}</span>
            {category !== undefined && (
              <span className="font-mono text-xs text-muted-foreground">
                id {category.id}
              </span>
            )}
          </p>
        ) : (
          /* Reachable on a row the screen did not create: the PATCH endpoint
             accepts `category_id: null`, so an alias can be pointed at a
             merchant and carry no category of its own. */
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            No category of its own, so this string matches nothing on its own. It
            only means something if a merchant is named beside it.
          </p>
        )}

        {/* The merchant it was filed under, in words. An alias that names a
            merchant can inherit its category, and the difference between "I
            chose this category" and "this came from that merchant" is the thing
            a reader needs before deleting the row. */}
        {merchantId !== null && (
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Filed under the merchant{" "}
            {merchantName === null ? (
              <>
                <span className="font-mono">{merchantId}</span>, which this screen
                could not read
              </>
            ) : (
              <>
                <span className="font-medium text-ink-quiet">{merchantName}</span>{" "}
                <span className="font-mono">{merchantId}</span>
              </>
            )}
            .
          </p>
        )}

        {/* What the score DOES, not just what it is. Two carriers for the
            distinction — the number and the words — so it survives a monochrome
            print and a screen reader. */}
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
          <span className="font-mono tabular-nums">confidence {confidence}</span>{" "}
          {automatic ? (
            <>— at or above {AUTO_BAR}, so a matching transaction is filed without asking.</>
          ) : (
            <>
              — below {AUTO_BAR}, so a matching transaction goes to the review queue
              instead of being filed.
            </>
          )}
        </p>
      </div>

      <button
        type="button"
        className="btn btn-quiet shrink-0 px-2.5 py-1.5 text-xs"
        disabled={busy}
        onClick={() => onDelete(aliasId, rawString)}
        aria-label={`Delete the alias ${rawString}`}
      >
        Delete
      </button>
    </li>
  );
};
