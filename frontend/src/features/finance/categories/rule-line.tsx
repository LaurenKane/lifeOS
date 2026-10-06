/* Rule line — one stored rule, read as the sentence it is.
 *
 * THE SCREEN'S ACCEPTANCE IS "READABLE AS PLAIN TEXT", AND THIS IS WHAT THAT
 * LOOKS LIKE.
 *
 * A rule is not a row of columns to be parsed; it is an instruction with a
 * subject, and this renders it in the order it is meant:
 *
 *     "PAYPAL XYZ"  →  Music
 *
 * The pattern is the subject and it is set in the MONO at a step above the
 * surrounding text, because it is machine text — raw statement content that
 * the matcher compares against byte for byte, and the one field on this screen
 * a user has to type exactly. The category it points at is ink at body size,
 * because that is the thing a person reads to know what the rule does.
 *
 * LEARNED AND HAND ARE DISTINGUISHED THREE TIMES OVER, and it is worth saying
 * why all three are here. `is_learned` is the most important distinction on the
 * screen — one row is a decision the user made, the other is the machine
 * remembering one — and it is carried by:
 *
 *   1. THE WORD. "learned" against "hand-written". Colour is never the sole
 *      carrier of meaning anywhere in this app, so the word leads.
 *   2. THE FILL. A learned rule sits on a Paper Tint; a hand rule sits on the
 *      panel. Neither is a brand colour — lime and cyan are whole panels here,
 *      and a chip in either would be a brand colour on a small dot.
 *   3. WHERE THE ROW SITS. Every learned rule carries the same consequence,
 *      and that consequence is stated once on the panel rather than on 40 rows.
 *
 * WHAT IS NOT SHOWN, AND WHY.
 * `confidence` is on every learned row. The matcher stores it and the API
 * returns it, but a learned rule written by `build_learned_rule` is always
 * `1.00` — the system only teaches what it was told — so printing it would be
 * 40 identical numbers wearing the costume of information. It is still rendered
 * whenever it is NOT 1.00, because a rule the server scored lower is a fact
 * worth surfacing the moment one exists.
 *
 * THE DELETE BUTTON IS NOT ROW-DECORATIVE, AND IT IS NOT HALF-ENABLED.
 * `DELETE /categories/rules/{rule_id}` removes exactly ONE rule and addresses it
 * by an integer. The button is therefore live on every row, including the two
 * that used to carry an apology: a pattern holding a slash (`bakker/straat`) and
 * a rule with no pattern at all. Neither is unaddressable by id — a slash splits
 * one path segment in two, and a missing pattern is only a fact about what the
 * rule matches on — so a disabled button on either row would be a control
 * refusing to do something the ledger will do.
 *
 * The id is in the button's accessible name for the case where two rows are
 * otherwise identical: two rules may share one pattern, and "Delete" on both
 * names the same thing twice. */
import React from "react";
import { cn } from "@/lib/utils";
import type { Category, CategoryRule } from "./types";

/** The default confidence of every rule this application teaches. A learned rule
 * below this is a fact the matcher produced, not a fact the user gave it. */
const LEARNED_CONFIDENCE = "1.00";

export const RuleLine: React.FC<{
  rule: CategoryRule;
  category: Category | undefined;
  busy: boolean;
  onDelete: (rule: CategoryRule) => void;
  /** The list's own cadence, passed in so the stagger lives with the other two
   * lists that use the same pair of constants. */
  style?: React.CSSProperties;
}> = ({ rule, category, busy, onDelete, style }) => {
  const learned = rule.is_learned;
  const pattern = rule.description_pattern;
  /* A rule with no description pattern matches on its account or merchant
   * criterion, and there is no text to print — but it is still a rule, it is
   * still on the screen, and by id the ledger can remove it. So the row says what
   * it matches on rather than apologising for something it can now do. */
  const unmatchable = pattern === null || pattern.trim() === "";

  return (
    <li
      className={cn(
        "rise-row relative grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 px-5 py-4",
        learned ? "bg-muted/60" : undefined,
      )}
      style={style}
    >
      {/* The pattern and its kind. Two columns is the whole layout: everything
          that is the rule on the left, everything that is the action on the
          right. A third column for the priority number was tried and dropped —
          priority is metadata about the match order, and the panel header
          already explains it once. */}
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span
            className={cn(
              "rounded-sm border px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-[0.1em]",
              learned
                ? "border-border bg-panel text-muted-foreground"
                : "border-ink/25 bg-panel text-foreground",
            )}
          >
            {learned ? "Learned" : "Hand-written"}
          </span>
          {learned && rule.confidence !== LEARNED_CONFIDENCE && (
            /* The chip's rarity is the point: at 1.00 it is printed nowhere, so
               the one row that shows it is a row the server scored as uncertain. */
            <span className="font-mono text-[0.625rem] tabular-nums text-muted-foreground">
              confidence {rule.confidence}
            </span>
          )}
        </div>

        {/* The subject. Mono, one step up, and allowed to break anywhere: this
            is raw statement text and must never be clipped — a pattern whose
            tail is cut off is a pattern the user cannot check against the
            statement it came from.

            ONLY A RULE WITH NO PATTERN AT ALL gets the placeholder, and every rule
            above that line is deletable. */}
        <p
          className={cn(
            "mt-1.5 font-mono text-[0.9375rem] leading-snug break-all",
            unmatchable ? "text-muted-foreground" : "text-foreground",
          )}
        >
          {unmatchable ? "no text pattern" : pattern}
        </p>

        {/* The arrow and the destination. This is the sentence: what it points
            at. `→` is a real arrow in both vendored faces and needs no icon. */}
        <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-sm">
          <span aria-hidden className="text-muted-foreground">
            →
          </span>
          <span className="font-medium">{category?.name ?? `category ${rule.category_id}`}</span>
          {/* The id is kept beside the name on purpose. It is what the delete
              endpoint and every other screen agree on, and a name the ledger
              does not know yet — a category this build could not read — says so
              instead of rendering as a blank. */}
          <span className="font-mono text-xs text-muted-foreground">
            {category === undefined ? "" : `id ${category.id}`}
          </span>
        </p>

        {unmatchable && (
          /* Stated in the row rather than hidden. A rule that matches on its
             account or merchant is a real rule the user may want gone, and it
             looks like any other row apart from this sentence. */
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            This rule matches on its account or merchant rather than on text, so
            there is no pattern to read here. It can still be deleted.
          </p>
        )}
      </div>

      {/* The action, and the number that governs the match order. The priority
          sits under the button rather than in a column of its own: it is a fact
          about the list, read once down the edge, and a number in a column would
          invite comparing it across rows as though the gaps meant something. */}
      <div className="flex shrink-0 flex-col items-end gap-2">
        <button
          type="button"
          className="btn btn-quiet px-2.5 py-1.5 text-xs"
          disabled={busy}
          onClick={() => onDelete(rule)}
          /* The id leads the name because two rules can share one pattern, and
             "delete the rule matching paypal xyz" twice on one screen is a label
             that tells a screen-reader user nothing about which row they are on.
             The endpoint takes this very number, so it is also the one thing a
             reader can check the delete against. */
          aria-label={`Delete rule ${rule.id}, matching ${pattern ?? "its account or merchant"}`}
        >
          Delete
        </button>
        <span
          className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground"
          title={`Priority ${rule.priority}. The engine matches in ascending priority order, so a lower number is tried first.`}
        >
          priority {rule.priority}
        </span>
      </div>
    </li>
  );
};