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
 * THE DELETE BUTTON IS NOT ROW-DECORATIVE.
 * `DELETE /categories/rules/{pattern}` removes EVERY rule carrying that text,
 * hand and learned alike, and it addresses them by text rather than by id. So
 * the count of affected rules is stated in the button's own accessible name and
 * the confirmation says it in words before anything is sent. */
import React from "react";
import { cn } from "@/lib/utils";
import type { Category, CategoryRule } from "./types";

/** The default confidence of every rule this application teaches. A learned rule
 * below this is a fact the matcher produced, not a fact the user gave it. */
const LEARNED_CONFIDENCE = "1.00";

export const RuleLine: React.FC<{
  rule: CategoryRule;
  category: Category | undefined;
  /** How many rules carry this pattern. Deleting one removes all of them, so the
   * count is stated rather than implied. */
  sharing: number;
  busy: boolean;
  onDelete: (rule: CategoryRule) => void;
  /** The list's own cadence, passed in so the stagger lives with the other two
   * lists that use the same pair of constants. */
  style?: React.CSSProperties;
}> = ({ rule, category, sharing, busy, onDelete, style }) => {
  const learned = rule.is_learned;
  const pattern = rule.description_pattern;
  /* A rule with no description pattern matches on its account or merchant
   * criterion, and `DELETE /rules/{pattern}` addresses rules BY TEXT — so there
   * is nothing this screen can address. Stated in the row rather than hidden:
   * a rule the user cannot see is a rule they will not fix, and a button that
   * cannot do what it says is worse than neither. */
  const unmatchable = pattern === null || pattern.trim() === "";
  /* A SLASH IS ALSO UNADDRESSABLE, and this one is the server's shape rather
   * than the column's.
   *
   * Verified against the running backend on 2026-10-06: a rule whose pattern is
   * `bakker/straat` exists, is listed by `GET /rules`, and cannot be deleted by
   * `DELETE /rules/{pattern}` — encoded, double-encoded, or as a literal path.
   * The path parameter is one segment, so a slash in it addresses a route that
   * does not exist and answers 404 for a rule that is plainly on screen.
   *
   * This screen therefore does not offer the button, and says why in words. The
   * alternative — offer it, watch it 404, and report "no rule matching
   * 'bakker/straat'" — would be a screen telling the user a rule does not exist
   * while listing it three lines above. That is the specific dishonesty this
   * product's empty states exist to prevent.
   *
   * A backend endpoint that addresses a rule by `id` rather than by text would
   * close this. It does not exist today, and inventing one here would be worse
   * than naming the gap. */
  const unslashable = pattern !== null && pattern.includes("/");
  const addressable = !unmatchable && !unslashable;

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

            ONLY A RULE WITH NO PATTERN AT ALL gets the placeholder. A rule whose
            pattern merely cannot be DELETED still shows its pattern, because
            showing it is the screen's whole job and hiding readable text because
            of a limitation in one control would be its own kind of dishonesty. */}
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
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            This rule matches on its account or merchant, not on text, and the
            delete endpoint takes a pattern — so this screen cannot remove it.
          </p>
        )}
        {unslashable && (
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            Its pattern holds a slash, and the delete endpoint takes one path
            segment — so the ledger cannot address this rule for deletion either.
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
          disabled={busy || !addressable}
          onClick={() => onDelete(rule)}
          aria-label={
            sharing > 1
              ? `Delete ${sharing} rules matching ${pattern ?? "this rule"}`
              : `Delete the rule matching ${pattern ?? "this rule"}`
          }
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