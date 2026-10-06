/* App shell — the one piece of chrome every route shares.
 *
 * The route table in `src/routes/index.tsx` is flat by design, so there is no
 * layout route to hang a header on: each page renders `<AppShell>` itself. That
 * keeps the table flat and the navigation in one file.
 *
 * THE MASTHEAD IS ONE LINE AND NO BAND.
 * The incumbent world put a blurred, bordered strip here. This one has no
 * background of its own, no border under it and no blur: the ground is the ground
 * all the way to the top of the viewport, and the masthead separates from the
 * page by whitespace alone. A band would be a fifth surface in a world whose
 * entire separation mechanism is the 2–3% lightness step between ground and
 * panel, and a band breaks that by being a different value again.
 *
 * HEADINGS. There is exactly one `<h1>` per document and it is the wordmark,
 * because on every page in this app the wordmark IS what the document is called.
 * Feature pages' own titles are therefore `<h2>` (`PageHeader`), and the panels
 * inside them are `<h3>`. Two `h1`s on one page is the usual outcome of a shared
 * shell that also renders a page title, and this is the fix rather than the
 * symptom.
 */
import React from "react";
import { NavLink } from "react-router-dom";

/* ONE ENTRY FOR TWO SCREENS, AND THE `end` PROP IS WHAT MAKES IT ONE.
 *
 * `/finance/categories` and `/finance/categories/rules` are two addresses for
 * one subject, and a masthead that lists both would be a navigation offering
 * the same choice twice. "Categories" goes to the inventory; the rules screen
 * links to it and back, so a user who arrived at the rules by typing a URL is
 * one click from the categories and vice versa.
 *
 * `end` is set on the link, so "Categories" stays quiet while the rules screen
 * is open. Without it React Router would mark both active, which on a masthead
 * means two lit items and no indication of which page you are on. */
const NAV: ReadonlyArray<{ to: string; label: string }> = [
  { to: "/", label: "Overview" },
  { to: "/finance/accounts", label: "Accounts" },
  { to: "/finance/transactions", label: "Transactions" },
  { to: "/finance/review", label: "Review" },
  { to: "/finance/categories", label: "Categories" },
  /* Its own entry rather than a second link under "Categories": merchants are a
   * different KIND of thing from both the category inventory and the rules — they
   * are what the matcher matches a payee against — and burying a third curation
   * screen behind the first would make it a place a user has to know exists. */
  { to: "/finance/merchants", label: "Merchants" },
  { to: "/finance/imports", label: "Imports" },
  { to: "/finance/budgets", label: "Budgets" },
];

/**
 * A nav link. The active route is ink at full weight; an inactive one is the
 * quiet ink and gains weight on hover.
 *
 * Two carriers for the active state — colour AND weight — because the current
 * route is information a reader needs before they click, and colour alone fails
 * in a monochrome print. No underline and no pill: this is a form field being
 * filled in, and in this world a filled field is a colour fill.
 */
const linkClass = ({ isActive }: { isActive: boolean }): string =>
  [
    "rounded px-2 py-1 transition-colors duration-150",
    "text-[0.75rem] tracking-[0.02em]",
    isActive ? "font-semibold text-ink" : "font-medium text-ink-quiet hover:text-ink",
  ].join(" ");

export const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="min-h-dvh">
    <header className="px-5 pt-6 sm:px-8 lg:px-10">
      <div className="mx-auto flex max-w-[88rem] flex-wrap items-center justify-between gap-x-8 gap-y-4">
        <h1 className="m-0">
          <NavLink
            to="/"
            className="block text-[1.0625rem] font-semibold tracking-[-0.02em] whitespace-nowrap text-ink transition-colors hover:text-ink-quiet"
          >
            Life OS
          </NavLink>
        </h1>
        {/* No negative margin on this row. Optical overhang is a nicety and an
            element wider than its parent is a layout bug, and at 390px with six
            sections the bug wins. The gap is the gap. */}
        <nav aria-label="Sections" className="flex flex-wrap items-center gap-x-1 gap-y-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              /* Exact matching on every entry, not just the root. A prefix match
                 would light "Transactions" while the record page is open and
                 "Categories" while the rules screen is — the same two-lit-item
                 defect the masthead comment above describes. */
              end
              className={linkClass}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>

    <main className="mx-auto max-w-[88rem] px-5 pt-8 pb-24 sm:px-8 lg:px-10 lg:pt-12">
      {children}
    </main>
  </div>
);