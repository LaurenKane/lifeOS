/* Presentational building blocks shared by the feature pages.
 *
 * These exist because the four feature slices all need the same seven things —
 * a page heading, a bordered surface, a notice, an empty state, a skeleton, a
 * labelled field, and an amount — and the alternative is the same Tailwind class
 * string copied into every page with a slightly different padding each time.
 *
 * They are plain Tailwind utilities. No component library is installed: there is
 * no `src/components/ui/`, and `package.json` has no `lucide-react`,
 * `@radix-ui/*`, `clsx` or `tailwind-merge`, so anything that looks like a
 * primitive here is a local function.
 *
 * THE FLAT RULE, APPLIED HERE
 * --------------------------
 * `Panel` had a border and a two-layer drop shadow in the incumbent world. It has
 * neither now: a panel is a fill and a 6px radius, and it separates from the
 * ground by the 2–3% lightness step between `--panel` and `--ground`. A shadow
 * here would not add depth, it would add a fourth way of saying "this is a box"
 * and undo the flatness that the two brand fills are doing the work with.
 *
 * HEADINGS. `PageHeader` renders an `<h2>` because `AppShell` renders the
 * document's `<h1>` — the wordmark. `Panel` renders `<h3>` for the same reason.
 * One `h1` per document, and the outline means something.
 */
import React from "react";
import { amountParts } from "@/lib/money";
import { cn } from "@/lib/utils";

/** The page's own title, in the grotesque, with whatever prose frames it and
 * whatever single action the page exists to perform.
 *
 * There is deliberately no kicker above the `<h2>`. An eyebrow that only repeats
 * the heading's own words — "Finance / Transactions" over "Transactions" — adds a
 * second, smaller voice saying the thing the heading already names at full size,
 * and the reader's eye lands on the quieter one. Anything genuinely worth
 * labelling belongs in the page body as data: a definition row, a field label, a
 * status chip. The `.eyebrow` utility survives for those.
 */
export const PageHeader: React.FC<{
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}> = ({ title, description, actions }) => (
  <div className="mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
    <div className="max-w-[68ch]">
      {/* Generous space ABOVE the heading comes from the section that owns this
          block; what is left below it is the heading's own leading plus the
          description's small gap. The reading order is top-down and the space
          says so. */}
      <h2 className="text-[1.75rem] leading-tight font-semibold tracking-[-0.025em] text-balance">
        {title}
      </h2>
      {description !== undefined && (
        <p className="mt-2 text-[0.875rem] leading-relaxed text-ink-quiet">{description}</p>
      )}
    </div>
    {actions !== undefined && <div className="flex items-center gap-2">{actions}</div>}
  </div>
);

/** A surface. A fill and a radius, and nothing else. */
export const Panel: React.FC<{
  title?: string;
  description?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}> = ({ title, description, className, children }) => (
  <section
    className={cn("rounded-md bg-panel", className)}
  >
    {(title !== undefined || description !== undefined) && (
      <header className="px-6 pt-6 pb-4">
        {title !== undefined && (
          <h3 className="text-[1.0625rem] font-semibold tracking-[-0.01em]">{title}</h3>
        )}
        {description !== undefined && (
          <p className="mt-1.5 max-w-[68ch] text-[0.8125rem] leading-relaxed text-ink-quiet">
            {description}
          </p>
        )}
      </header>
    )}
    {children}
  </section>
);

/** A notice's tone is carried three times over — the tint fill, the tone-named
 * label, and the words themselves — so it survives a monochrome print and a
 * screen reader. There is no border and no left bar: the tint is the frame, and
 * a 1px rule around a 10% wash adds nothing the wash has not already said. */
const NOTICE_TONE = {
  error: {
    fill: "bg-money-out/10",
    label: "text-money-out",
  },
  warning: {
    fill: "bg-money-out/6",
    label: "text-money-out",
  },
  info: {
    fill: "bg-ground",
    label: "text-ink-quiet",
  },
} as const;

export const Notice: React.FC<{
  tone?: keyof typeof NOTICE_TONE;
  label?: string;
  children: React.ReactNode;
}> = ({ tone = "info", label, children }) => (
  <div role={tone === "error" ? "alert" : "status"} className={cn("rounded-md px-4 py-3", NOTICE_TONE[tone].fill)}>
    {label !== undefined && <p className={cn("eyebrow", NOTICE_TONE[tone].label)}>{label}</p>}
    <div className="mt-1.5 whitespace-pre-line text-[0.8125rem] leading-relaxed">
      {children}
    </div>
  </div>
);

export const EmptyState: React.FC<{
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}> = ({ title, children, action }) => (
  <div className="px-6 py-12 text-center">
    <p className="text-[1.0625rem] font-semibold tracking-[-0.01em]">{title}</p>
    {children !== undefined && (
      <div className="mx-auto mt-2 max-w-[46ch] text-[0.8125rem] leading-relaxed text-ink-quiet">
        {children}
      </div>
    )}
    {action !== undefined && <div className="mt-5 flex justify-center">{action}</div>}
  </div>
);

/** Loading is a placeholder shaped like the thing being loaded — bars where rows
 * will be — rather than the word "Loading".
 *
 * `role="status"` takes no name from its contents, so the label is an
 * `aria-label`: the live region announces itself as "Loading transactions" rather
 * than reading four empty bars aloud. The pulse is the one decorative animation
 * in the app and it is dropped entirely under `prefers-reduced-motion`. */
export const Skeleton: React.FC<{ label: string; rows?: number }> = ({ label, rows = 4 }) => (
  <div role="status" aria-live="polite" aria-busy="true" aria-label={label}>
    <div className="flex flex-col gap-3 px-6 py-5">
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex animate-pulse items-center gap-4">
          <div className="h-2.5 w-20 rounded bg-ink-quiet/15" />
          <div className="h-2.5 flex-1 rounded bg-ink-quiet/15" />
          <div className="h-2.5 w-24 rounded bg-ink-quiet/15" />
        </div>
      ))}
    </div>
  </div>
);

export const Field: React.FC<{
  label: string;
  htmlFor: string;
  hint?: React.ReactNode;
  error?: string | null;
  children: React.ReactNode;
}> = ({ label, htmlFor, hint, error, children }) => (
  <div>
    <label htmlFor={htmlFor} className="eyebrow block">
      {label}
    </label>
    <div className="mt-1.5">{children}</div>
    {error !== undefined && error !== null ? (
      <p className="mt-1.5 text-xs text-money-out">{error}</p>
    ) : hint !== undefined ? (
      <p className="mt-1.5 text-xs leading-relaxed text-ink-quiet">{hint}</p>
    ) : null}
  </div>
);

/**
 * One signed amount.
 *
 * The sign is the loudest thing about it and the colour is the second: an amount
 * is right-aligned in the monospace with tabular figures, so a column of them
 * lines up on the decimal point and a flipped sign cannot hide in a column. Zero
 * is neutral; money out is `--money-out`; money in is `--money-in`.
 *
 * The monospace is not a costume for "technical". It is the face that makes the
 * decimal points stack, and a ledger that cannot be scanned down a column is
 * broken regardless of how it looks (ARCHITECTURE.md §6).
 */
export const Amount: React.FC<{
  minor: number;
  currency: string;
  className?: string;
}> = ({ minor, currency, className }) => {
  const { sign, digits, code } = amountParts(minor, currency);
  const tone =
    minor < 0 ? "text-money-out" : minor > 0 ? "text-money-in" : "text-ink-quiet";
  return (
    <span className={cn("font-mono tabular-nums whitespace-nowrap", tone, className)}>
      {/* The glyph carries the direction to everyone else; a screen reader is
          told in words, because "−" is read as a dash by some of them. */}
      <span className="sr-only">
        {minor < 0 ? "money out " : minor > 0 ? "money in " : ""}
      </span>
      {sign}
      {digits}
      <span className="ml-1 text-[0.75em] tracking-[0.06em] opacity-75">{code}</span>
    </span>
  );
};

/**
 * A disclosure: one visible line that names a caveat, and the reasoning behind it
 * folded away until asked for.
 *
 * WHY THIS IS A PRIMITIVE AND NOT AN INLINE `<details>`
 * --------------------------------------------------
 * A chart that is honest about its own scale has to say so, and the sentences that
 * do that are long. The first version of the net-worth axis printed all of it:
 * five lines of methodology under the chart, on a page someone opens to read a
 * number. The reasoning was right and it was in the reading path, which is the one
 * place a page cannot afford to be right about nothing. It belongs one click away.
 *
 * `summary` IS THE VISIBLE LINE, and it is not a generic "More". It states the
 * caveat — the thing a reader would otherwise be misled by — so the disclosure is
 * findable by the question it answers rather than by the word "details".
 *
 * The affordance is a NATIVE `<details>`, so open and close, keyboard operation and
 * the expanded state for assistive technology come from the platform rather than
 * from a button and some state. The marker is the one thing taken away: the
 * browser's disclosure triangle is a system glyph drawn by the OS at a weight and
 * optical size that do not belong to this design, and the same rule that sent the
 * arrows to `icons.tsx` sends this here.
 */
export const Disclosure: React.FC<{
  /** The visible line. Must name the caveat, not the mechanism. */
  summary: string;
  children: React.ReactNode;
  className?: string;
}> = ({ summary, children, className }) => (
  <details className={cn("group border-t border-rule", className)}>
    <summary
      className={cn(
        "flex cursor-pointer list-none items-start gap-1.5 py-2 text-[0.8125rem] leading-relaxed",
        "marker:content-none",
        "[&::-webkit-details-marker]:hidden",
        "text-ink-quiet transition-colors hover:text-ink",
        "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink",
      )}
    >
      <span className="flex-1">{summary}</span>
      {/* One authored chevron, rotating on `open`. `group-open` is the only state
          hook it needs and it is a pure CSS transition, so the disclosure animates
with no script. `index.css` drops that transition under
          `prefers-reduced-motion: reduce` along with the page's entrances. */}
      <svg
        viewBox="0 0 16 16"
        width="12"
        height="12"
        aria-hidden="true"
        focusable="false"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-[0.2em] shrink-0 transition-transform duration-150 group-open:rotate-180"
      >
        <path d="M4 6l4 4 4-4" />
      </svg>
    </summary>
    <div className="max-w-[62ch] pb-1 text-[0.8125rem] leading-relaxed text-ink-quiet">
      {children}
    </div>
  </details>
);