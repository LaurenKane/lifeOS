/* App shell — the one piece of chrome every route shares.
 *
 * The route table in `src/routes/index.tsx` is flat by design, so there is no
 * layout route to hang a header on: each page renders `<AppShell>` itself.
 * That keeps the table flat and the navigation in one file.
 *
 * The masthead is a band of paper with a hairline under it, the wordmark set in
 * the display serif at small caps, and navigation in the same eyebrow style the
 * section headers use. Nothing here has state.
 */
import React from "react";
import { NavLink } from "react-router-dom";

const NAV: ReadonlyArray<{ to: string; label: string }> = [
  { to: "/", label: "Overview" },
  { to: "/finance/accounts", label: "Accounts" },
  { to: "/finance/transactions", label: "Transactions" },
  { to: "/finance/review", label: "Review" },
  { to: "/finance/imports", label: "Imports" },
  { to: "/finance/budgets", label: "Budgets" },
];

const linkClass = ({ isActive }: { isActive: boolean }): string =>
  [
    "relative py-1 transition-colors duration-150",
    "text-[0.6875rem] font-semibold uppercase tracking-[0.14em]",
    isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground",
    isActive ? "after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-primary" : "",
  ].join(" ");

export const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="min-h-dvh">
    <header className="border-b border-border bg-background/85 backdrop-blur-sm">
      <div className="mx-auto flex max-w-5xl flex-wrap items-baseline gap-x-8 gap-y-2 px-6 py-4">
        <NavLink to="/" className="font-display text-lg leading-none tracking-tight">
          Life&nbsp;OS
          <span className="ml-2 align-middle text-[0.6875rem] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Ledger
          </span>
        </NavLink>
        <nav aria-label="Sections" className="flex flex-wrap gap-x-6 gap-y-1">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={linkClass}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>

    <main className="mx-auto max-w-5xl px-6 pb-24 pt-10">{children}</main>
  </div>
);
