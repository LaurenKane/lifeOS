/* Group header — the kind a group belongs to, its count, and what the kind does.
 *
 * ONE HEADER SHARED BY BOTH PANELS, because the categorization surface now has
 * three screens that all group by kind and three copies of this header is three
 * places for the wording to drift. It says what the kind DOES rather than
 * repeating its name: "Expense" alone tells a reader nothing, and the sentence
 * is the one they need before choosing where something gets filed.
 *
 * The rule stands from `categories/page.tsx` — a header is not a place for a
 * nested heading level, because these groups are not document sections. It is a
 * `<p>` with a label class, so the page's outline stays: one `<h2>` from
 * `PageHeader`, `<h3>` per panel, and nothing below that pretending to be
 * structure. */
import React from "react";
import type { CurationGroup } from "./use-merchants";

export const GroupHeader: React.FC<{ group: CurationGroup<unknown> }> = ({ group }) => (
  <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-5 pb-2 pt-5">
    <span className="eyebrow text-foreground">{group.label}</span>
    <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
      {group.rows.length}
    </span>
    <p className="w-full text-[0.8125rem] leading-relaxed text-muted-foreground">
      {group.note}
    </p>
  </header>
);
