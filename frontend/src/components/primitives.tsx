/* Presentational building blocks shared by the feature pages.
 *
 * These exist because the four feature slices all need the same seven things —
 * a page heading, a bordered surface, a notice, an empty state, a skeleton, a
 * labelled field, and an amount — and the alternative is the same Tailwind
 * class string copied into every page with a slightly different padding each
 * time.
 *
 * They are plain Tailwind utilities. No component library is installed: there
 * is no `src/components/ui/`, and `package.json` has no `lucide-react`,
 * `@radix-ui/*`, `clsx` or `tailwind-merge`, so anything that looks like a
 * primitive here is a local function.
 */
import React from "react";
import { amountParts } from "@/lib/money";
import { cn } from "@/lib/utils";

export const PageHeader: React.FC<{
  eyebrow: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}> = ({ eyebrow, title, description, actions }) => (
  <div className="rise mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
    <div className="max-w-2xl">
      <p className="eyebrow">{eyebrow}</p>
      <h1 className="mt-1.5 font-display text-3xl leading-tight tracking-tight">{title}</h1>
      {description !== undefined && (
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
      )}
    </div>
    {actions !== undefined && <div className="flex items-center gap-2">{actions}</div>}
  </div>
);

export const Panel: React.FC<{
  title?: string;
  description?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}> = ({ title, description, className, children }) => (
  <section
    className={cn(
      "rounded-lg border border-border bg-background",
      "shadow-[0_1px_0_0_oklch(0_0_0/0.02),0_1px_3px_0_oklch(0.2_0.02_80/0.05)]",
      className,
    )}
  >
    {(title !== undefined || description !== undefined) && (
      <header className="border-b border-border px-5 py-3.5">
        {title !== undefined && <h2 className="font-display text-lg tracking-tight">{title}</h2>}
        {description !== undefined && (
          <p className="mt-1 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </header>
    )}
    {children}
  </section>
);

const NOTICE_TONE = {
  error: {
    frame: "border-destructive/35 bg-destructive/[0.045]",
    label: "text-destructive",
  },
  warning: {
    frame: "border-money-out/35 bg-money-out/[0.05]",
    label: "text-money-out",
  },
  info: {
    frame: "border-border bg-muted/60",
    label: "text-muted-foreground",
  },
} as const;

export const Notice: React.FC<{
  tone?: keyof typeof NOTICE_TONE;
  label?: string;
  children: React.ReactNode;
}> = ({ tone = "info", label, children }) => (
  <div
    role={tone === "error" ? "alert" : "status"}
    className={cn("rounded-md border-l-[3px] px-4 py-3", NOTICE_TONE[tone].frame)}
  >
    {label !== undefined && (
      <p className={cn("eyebrow", NOTICE_TONE[tone].label)}>{label}</p>
    )}
    <div className="mt-1 whitespace-pre-line text-[0.8125rem] leading-relaxed">
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
    <p className="font-display text-lg tracking-tight">{title}</p>
    {children !== undefined && (
      <div className="mx-auto mt-2 max-w-md text-[0.8125rem] leading-relaxed text-muted-foreground">
        {children}
      </div>
    )}
    {action !== undefined && <div className="mt-5 flex justify-center">{action}</div>}
  </div>
);

/** Loading is a placeholder shaped like the thing being loaded — a list of
 * hairline bars where rows will be — rather than the word "Loading".
 *
 * `role="status"` takes no name from its contents, so the label is an
 * `aria-label`: the live region announces itself as "Loading transactions"
 * rather than reading four empty bars aloud. */
export const Skeleton: React.FC<{ label: string; rows?: number }> = ({ label, rows = 4 }) => (
  <div role="status" aria-live="polite" aria-busy="true" aria-label={label}>
    <div className="divide-y divide-border">
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex items-center gap-4 px-5 py-4">
          <div className="h-2.5 w-20 animate-pulse rounded-full bg-muted" />
          <div className="h-2.5 flex-1 animate-pulse rounded-full bg-muted" />
          <div className="h-2.5 w-24 animate-pulse rounded-full bg-muted" />
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
      <p className="mt-1.5 text-xs text-destructive">{error}</p>
    ) : hint !== undefined ? (
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{hint}</p>
    ) : null}
  </div>
);

/**
 * One signed amount.
 *
 * The sign is the loudest thing about it and the colour is the second: an
 * amount is right-aligned in a monospace face with tabular figures, so a column
 * of them lines up on the decimal point and a flipped sign cannot hide in a
 * column. Zero is neutral; money out is `--money-out`; money in is `--money-in`.
 */
export const Amount: React.FC<{
  minor: number;
  currency: string;
  className?: string;
}> = ({ minor, currency, className }) => {
  const { sign, digits, code } = amountParts(minor, currency);
  const tone = minor < 0 ? "text-money-out" : minor > 0 ? "text-money-in" : "text-muted-foreground";
  return (
    <span className={cn("font-mono tabular-nums whitespace-nowrap", tone, className)}>
      {/* The glyph carries the direction to everyone else; a screen reader is
          told in words, because "−" is read as a dash by some of them. */}
      <span className="sr-only">
        {minor < 0 ? "money out " : minor > 0 ? "money in " : ""}
      </span>
      {sign}
      {digits}
      <span className="ml-1 text-[0.75em] tracking-wide opacity-70">{code}</span>
    </span>
  );
};
