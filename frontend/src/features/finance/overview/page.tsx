/* ── The Overview page ──────────────────────────────────────────────────────
 * The first screen, and the first surface of the new visual world.
 *
 * WHAT IS ON IT, IN THE ORDER THE USER DECIDED
 * -------------------------------------------
 * There is no greeting, no avatar, and no introduction. The first thing on the
 * page is a number. An opening that restates the page's own title is a header
 * wearing a greeting's clothes, and the user called that out by name.
 *
 * THE LEADING PANEL IS SPENDING, and that is a correction rather than a
 * preference. PRODUCT.md scopes V1 as spending, and the mid-month use is "check my
 * spending from the previous month". Net worth is `assets − liabilities` across
 * accounts a bank treats as separate worlds — the mechanism this surface exists to
 * prove, and still on the page — but with no savings or investment holdings it is
 * an M10 idea with less to say than the spending figure, and putting it in the
 * loudest position had it front-running V1. So: spending leads, net worth follows.
 *
 * THE ORDER IS DATA, NOT LAYOUT — see `Panel` and `LEAD_PANEL` below. The panels
 * carry an explicit rank and the page renders them in rank order, so promoting
 * net worth back to first when savings and investments land is a one-line change
 * to a table rather than a rewrite of the page.
 *
 * WHY FOUR PANELS AND NOT ONE GRID OF EQUAL CARDS
 * ------------------------------------------------
 * Cards are the lazy container and same-size cards of icon-plus-heading-plus-text
 * are the lazy page structure. The asymmetry here is the argument: one panel is
 * several times the area of the others because it holds the figure the page opens
 * on, and the perforated stub is perforated because it is the one thing that can
 * be detached and dismissed.
 *
 * THE HIERARCHY IS CARRIED BY FOUR THINGS, NOT BY SIZE ALONE
 * ---------------------------------------------------------
 * The leading panel: the only lime field, at display size, in the only face that
 * tabulates. Everything else is one step down the type scale, on panel white or
 * cyan, and none of it is allowed to reach the size of the leading figure.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Disclosure } from "@/components/primitives";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "@/components/icons";
import { amountParts } from "@/lib/money";
import { cn } from "@/lib/utils";
import { axisDomain, rangeShare } from "./axis";
import { useOverview } from "./use-overview";
import type { CashflowBucket, NetWorthPoint } from "./types";

const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** A shared empty array, so the memos below are not handed a fresh `[]` on every
 * render of an empty ledger and recomputed for nothing. */
const EMPTY_SERIES: NetWorthPoint[] = [];

/** How many categories the ranked list shows.
 *
 * Eight is a list; thirteen is a table of contents. The rest are counted in words
 * under it rather than dropped, because a list that stops without saying so looks
 * like a complete answer. */
const VISIBLE_CATEGORIES = 8;

/**
 * ONE TABLE, one place where the page's argument is declared.
 *
 * Each panel says two things about itself: how far down the page it sits (`rank`)
 * and which of the two columns it packs into (`column`). RANK 1 IS THE LEAD — it
 * is the lime field, the display size and the first thing in the DOM. Everything
 * else follows from this one table: the fill, the type scale, the track widths and
 * the reading order. The layout constants in the JSX below are generic; not one
 * of them names a panel.
 *
 * THE ORDER IS A CONTENT DECISION AND NOT A LAYOUT CONSTANT, which is the point.
 * Today spending leads because PRODUCT.md scopes V1 as spending and the mid-month
 * question is "what did I spend". Net worth is `assets − liabilities` across
 * accounts a bank treats as separate worlds — still the mechanism this surface
 * proves, and still second — but with no savings or investment holdings it is an
 * M10 idea with less to say, and leading with it had M10 front-running V1.
 *
 * When savings and investment holdings actually land — `LifeOS-l3j`, M9/M10 — net
 * worth becomes the figure with the most to say, and promoting it is a two-row edit
 * HERE: give `netWorth` rank 1 and `spend` rank 2. No component changes, no CSS,
 * no test.
 *
 * The two columns are not a grid of rows; each packs downwards on its own. See the
 * comment on the grid below for why that distinction is load-bearing.
 */
type PanelId = "spend" | "netWorth" | "months" | "categories" | "review";

/** `rank` is vertical order; `column` is the track. Rank 1 is the lead. */
const PANELS: Readonly<
  Record<PanelId, { rank: number; column: "wide" | "narrow" }>
> = {
  /* The lead carries the figure, so it is the only lime field and the only one
     with display type. It is sized by its four fields and nothing else. */
  spend: { rank: 1, column: "wide" },
  /* Rank 2 — the mechanism this surface proves, one step down the scale. Its six
     months ride beneath it in the same column because the series belongs to the
     figure above it. */
  netWorth: { rank: 2, column: "wide" },
  /* `when`, not `how much` — so it follows the two panels that are about the
     position, and sits at the top of the narrow column beside the lead. */
  months: { rank: 3, column: "narrow" },
  categories: { rank: 4, column: "narrow" },
  review: { rank: 5, column: "narrow" },
};

/** Panels in render order. Computed once, from the table above. */
const PANEL_ORDER: readonly PanelId[] = (Object.keys(PANELS) as PanelId[]).sort(
  (a, b) => PANELS[a].rank - PANELS[b].rank,
);

export const OverviewPage: React.FC = () => {
  const overview = useOverview();

  const panels: Record<PanelId, React.ReactNode> = {
    spend: <SpendPanel overview={overview} />,
    netWorth: <NetWorthPanel overview={overview} />,
    months: <MonthsPanel overview={overview} />,
    categories: <CategoriesPanel overview={overview} />,
    review: <ReviewStub overview={overview} />,
  };

  /* The two tracks are filled from the table's `column`, each keeping rank order
     within itself, so a panel moving up the page moves within its column and the
     reading order still falls out of the numbers rather than out of the markup. */
  const byColumn = (column: "wide" | "narrow"): React.ReactNode[] =>
    PANEL_ORDER.filter((id) => PANELS[id].column === column).map((id) => panels[id]);
  const wide = byColumn("wide");
  const narrow = byColumn("narrow");

  return (
    <AppShell>
      {/* THE demo marker. One of them, at the top, carrying the recovery action.
          An earlier draft said it four ways — this bar, a chip, a paragraph and a
          badge inside the amount box — which is not caution, it is four chances to
          miss the one that matters and four things to read before you can use the
          page. The honesty constraint is not being softened here; it is being
          said once, in the loudest element on the page, where nobody scrolls past
          it. */}
      {overview.demo && <DemoBanner onExit={() => overview.setDemo(false)} />}

      <div className="mb-6 flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
        <DemoToggle demo={overview.demo} onChange={overview.setDemo} />
      </div>

      {/* The asymmetric grid: two tracks, `minmax(0, …)` on both so a wide figure can
          shrink rather than forcing a track wider than the viewport — the single
          most common way a mono figure breaks a two-column layout.

          NOT A GRID OF ROWS BUT TWO COLUMNS, each packing downwards on its own.
          A grid row is as tall as its tallest cell, so anything in a second row
          inherits the first row's height — which is what put ~300px of dead space
          under the leading panel on an earlier pass. A flex column has no such
          floor. Every panel is sized by what it holds, and unequal column heights
          are correct: they are what makes the grid asymmetric.

          The wide track is 1.9fr because it holds the two panels that carry the
          figure and its proof, and a display-size mono figure needs the room. No
          class name in this markup names a panel — the assignment came from the
          table above. */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)] lg:items-start">
        <div className="flex flex-col gap-6">{wide}</div>
        <div className="flex flex-col gap-6">{narrow}</div>
      </div>
    </AppShell>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   The amount box — the loudest thing on the page.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * The figure, and nothing else.
 *
 * THE TWO LABELS IN THIS BOX, and why neither is a kicker. The craft floor bans a
 * label sitting above a heading; both of these sit BESIDE a label rather than
 * above a heading, and both name a real field:
 *
 *   `Net worth · EUR`  names what the figure is and what it is denominated in. The
 *                      heading here is the NUMBER, so this is its field label — and
 *                      it is the one place on the page that states the unit, which
 *                      is why no individual figure repeats `EUR`.
 *   `As at 5 Oct 2026` names the date the balance is as of, which is a different
 *                      fact from the balance and the reader cannot derive it. A net
 *                      worth without a date is a claim about no particular moment.
 *
 * The second one is a caption rather than a label, which is why it is set in the
 * same small caps but sits at the opposite end of the row: they are two fields of
 * one form, not a heading and something decorative above it.
 *
 * The figure is in the monospace with tabular figures, at `clamp()` between 2.5rem
 * and 5.5rem, which keeps a seven-figure net worth inside a 390px column without
 * shrinking to nothing and without ever exceeding the 6rem display ceiling.
 */
const NetWorthPanel: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  const latest = overview.netWorth.at(-1) ?? null;
  const delta = periodDelta(overview.netWorth);

  /* What the top-right corner is allowed to say. "Nothing recorded" is a claim
     about the ledger, so it is only printed when the ledger really is empty — a
     failed read gets a different word, because saying nothing was recorded when
     the truth is that nobody could ask is the exact substitution this page exists
     to avoid. */
  const stamp = (() => {
    if (overview.loading) return "Reading";
    if (overview.error !== null) return "Not available";
    if (overview.empty || latest === null) return "Nothing recorded";
    return `As at ${formatDay(latest.date)}`;
  })();

  return (
    <>
      {/* THE SECOND PANEL, and the one that proves the mechanism.
         `assets − liabilities` across accounts a bank treats as separate worlds,
         every line traceable to a raw statement record — that is what this
         application does that no bank app does, and it is why the figure is here
         at all. It is second because PRODUCT.md scopes V1 as spending, not
         because it matters less.

         Panel white, one step down the type scale from the lead. The rule
         enforced here is that this figure is `text-[2.75rem]` at the top of its
         own scale and never touches the lead's `clamp(2.5rem, 7.5vw, 5.5rem)`. */}
      <section
        aria-labelledby="net-worth-label"
        className="flex flex-col rounded-md bg-panel p-6 text-ink"
      >
        <div className="flex items-baseline justify-between gap-4">
          {/* Two fields of one form, side by side: what the figure is, and what it
              is denominated in. Neither is a kicker — there is no heading above
              either, and the figure itself is the loudest thing on the panel. */}
          <p id="net-worth-label" className="eyebrow text-ink-quiet">
            Net worth · EUR
          </p>
          <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-ink-quiet">
            {stamp}
          </p>
        </div>

        <div className="mt-6">
          {overview.loading ? (
            <AmountSkeleton />
          ) : overview.error !== null ? (
            <AmountError message={overview.error} onRetry={overview.reload} />
          ) : overview.empty || latest === null ? (
            <EmptyFigure />
          ) : (
            <p className="font-mono text-[2.75rem] leading-[0.95] font-medium tracking-[-0.03em] tabular-nums sm:text-[3.5rem]">
              <NetWorthFigure minor={latest.net_worth} />
            </p>
          )}

          {!overview.loading && overview.error === null && !overview.empty && latest !== null && (
            <DeltaLine delta={delta} />
          )}

          {/* What the figure is MADE OF. This is the whole point of the panel and
              it is why the account count is on it rather than in a footnote: a
              net worth you cannot decompose is a number, and this is the line
              that makes it a claim about named accounts. */}
          <p className="mt-6 border-t border-rule pt-4 text-[0.8125rem] leading-relaxed text-ink-quiet">
            {overview.loading
              ? "Reading the ledger…"
              : overview.error !== null
                ? "The account list was not read either, so there is nothing to count."
                : overview.accountCount === 0
                  ? "No accounts are counted yet."
                  : `Assets less liabilities, across ${overview.accountCount} ${
                      overview.accountCount === 1 ? "account" : "accounts"
                    }. `}
            {overview.accountCount > 0 && (
              <Link
                to="/finance/accounts"
                className="underline decoration-current/40 underline-offset-4 transition-colors hover:text-ink hover:decoration-current"
              >
                See them
              </Link>
            )}
          </p>
        </div>
      </section>

      {/* The series belongs to the figure above it, so it sits directly beneath it
          in the same column rather than in a row of its own. */}
      <NetWorthSeries overview={overview} />
    </>
  );
};

/** The figure itself.
 *
 * Split from the panel so the box's own label can carry the unit: this page states
 * `EUR` once per panel and never on a figure, which is why there is no currency
 * code down here at all. A seven-figure net worth is eleven glyphs of mono and
 * nothing else has to fit beside it.
 *
 * The sign is spoken, not drawn — a screen reader announces `−` as a dash, so a
 * negative net worth would be read as punctuation rather than as owing money. */
const NetWorthFigure: React.FC<{ minor: number }> = ({ minor }) => {
  const { digits } = amountParts(minor, "EUR");
  return (
    <>
      <span className="sr-only">{minor < 0 ? "negative, " : ""}</span>
      {minor < 0 ? "−" : ""}
      {digits}
    </>
  );
};

/**
 * The delta against the previous period.
 *
 * A comparison to 30 days back, taken from the same daily series rather than
 * remembered. The SIGN carries the direction, an authored arrow carries it
 * second, and the word carries it third — three carriers, so the direction
 * survives a monochrome print and a screen reader. The colour is a fourth.
 */
const DeltaLine: React.FC<{ delta: number | null }> = ({ delta }) => {
  if (delta === null) {
    return <p className="mt-3 text-[0.9375rem] text-lime-ink">No earlier point to compare against.</p>;
  }

  if (delta === 0) {
    return (
      <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-[0.9375rem] text-lime-ink-deep">
        <span>Unchanged</span>
        <span className="text-lime-ink">against 30 days ago</span>
      </p>
    );
  }

  const up = delta > 0;
  const Arrow = up ? ArrowUpRight : ArrowDownRight;

  return (
    <p
      className="mt-3 flex flex-wrap items-baseline gap-x-2 text-[0.9375rem]"
      /* Not `aria-live`: this figure is not going to change under a reader who is
         looking at it, and announcing it on every settle would be noise. */
    >
      <span className={cn("inline-flex items-center gap-1", up ? "text-lime-ink-deep" : "text-money-out")}>
        <Arrow className="size-4 shrink-0" />
        <span className="font-mono tabular-nums">
          <Figure minor={delta} signed />
        </span>
      </span>
      <span className="text-lime-ink">
        {up ? "more than" : "less than"} 30 days ago
      </span>
    </p>
  );
};

/** The account count, which is the line that makes the figure checkable. Without
 * it, a net worth is a claim; with it, it is a claim about five named accounts
 * the reader can go and open. */
const AmountSkeleton: React.FC = () => (
  <div role="status" aria-label="Reading net worth" aria-busy="true" className="flex-1">
    <div className="h-14 w-3/4 animate-pulse rounded-md bg-lime-ink/20 sm:h-16" />
    <div className="mt-4 h-4 w-40 animate-pulse rounded-md bg-lime-ink/15" />
  </div>
);

const AmountError: React.FC<{ message: string; onRetry: () => void }> = ({ message, onRetry }) => (
  <div className="flex-1" role="alert">
    <p className="text-[1.0625rem] font-medium text-ink">
      The ledger could not be read, so there is no figure to show.
    </p>
    <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-lime-ink-deep">{message}</p>
    <button type="button" onClick={onRetry} className="btn mt-4 bg-ink text-lime hover:bg-ink/85">
      Try again
    </button>
  </div>
);

/**
 * The honest empty state, and the reason it is not a zero.
 *
 * `0.00` would be a claim: it would say the user is worth nothing, when the truth
 * is that nothing has been recorded. The box says what is missing and where to
 * put it, and it names the two steps in the order they have to happen — an
 * account first, because a transaction cannot exist without one.
 */
const EmptyFigure: React.FC = () => (
  <div className="flex-1">
    <p className="text-[1.375rem] leading-snug font-medium text-ink">
      Nothing to add up yet.
    </p>
    <p className="mt-2 max-w-[38ch] text-[0.8125rem] leading-relaxed text-lime-ink-deep">
      This ledger has no accounts, so there is no balance to report. Register one
      and record a transaction against it, and this box will carry the figure.
    </p>
    <Link
      to="/finance/accounts"
      className="btn mt-4 bg-ink text-lime hover:bg-ink/85 focus-visible:outline-lime"
    >
      Register an account
      <ArrowRight className="size-4" />
    </Link>
  </div>
);

/* ═══════════════════════════════════════════════════════════════════════════
   The leading panel — SPENDING.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * What was spent, in a period, and what that is against the period before it.
 *
 * THE PANEL THE PAGE OPENS ON, and the lime field. FOUR THINGS, in this order and
 * nothing else:
 *
 *   the period · the amount spent in it · the comparison against the previous
 *   period · the top category that consumed it
 *
 * The last one is the "traceable to a statement" requirement doing its work: the
 * headline number is immediately attached to something the reader can go and open.
 *
 * It leads because PRODUCT.md scopes V1 as spending and the mid-month question is
 * "what did I spend", not "what am I worth". See the file header for why that was
 * a correction rather than a preference.
 *
 * THE SIX-MONTH BAR CHART IS NOT IN HERE, and its absence is a requirement rather
 * than a simplification. It lived in this panel for one round and pushed the lime
 * field to about 640px — which made it the TALLEST panel as well as the loudest,
 * and those are different properties. The brief asks for the single loud region,
 * not the single big one, and the user has twice said the cards are too big. The
 * four fields above are the whole of what this panel is for; the bars answer a
 * different question — when, not how much — and they have their own panel now, in
 * `MonthsPanel`. At about 300px this panel sits level with the net-worth figure
 * beside it, so the two wide panels read as equals and one carries the emphasis.
 */
const SpendPanel: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  const { cashflow, spendByCategory, loading, error, reload } = overview;
  const current = cashflow.at(-1) ?? null;
  /* The period before the one on display — a real comparison from the same
   * response rather than a remembered number. */
  const previous = current === null ? null : (cashflow.at(-2) ?? null);
  const top = React.useMemo(() => {
    const expenses = spendByCategory.filter((point) => point.amount < 0);
    return expenses.reduce<(typeof expenses)[number] | null>(
      (worst, point) => (worst === null || point.amount < worst.amount ? point : worst),
      null,
    );
  }, [spendByCategory]);

  const change =
    current === null || previous === null || previous.expense === 0
      ? null
      : ((current.expense - previous.expense) / previous.expense) * 100;

  return (
    <section aria-labelledby="spend-heading" className="flex flex-col rounded-md bg-lime p-6 text-ink lg:p-8">
      <div className="flex items-baseline justify-between gap-4">
        {/* Two fields of one form, side by side: what this panel is, and which
            period it covers. Neither is a kicker — there is no heading above
            either. */}
        <p id="spend-heading" className="eyebrow text-lime-ink-deep">
          Spent · EUR
        </p>
        <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-lime-ink">
          {loading
            ? "Reading"
            : error !== null
              ? "Not available"
              : current === null
                ? "Nothing recorded"
                : monthLabel(current.period)}
        </p>
      </div>

      <div className="mt-8">
        {loading ? (
          <p role="status" aria-label="Reading spending" aria-busy="true" className="text-[0.9375rem] text-lime-ink">
            Reading the ledger…
          </p>
        ) : error !== null ? (
          <div role="alert">
            <p className="text-[1.0625rem] font-medium text-ink">
              The ledger could not be read, so there is nothing to report.
            </p>
            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-lime-ink-deep">{error}</p>
            <button type="button" onClick={reload} className="btn mt-4 bg-ink text-lime hover:bg-ink/85">
              Try again
            </button>
          </div>
        ) : current === null ? (
          <EmptySpend />
        ) : (
          <>
            <p className="font-mono text-[clamp(2.5rem,7.5vw,5.5rem)] leading-[0.95] font-medium tracking-[-0.03em] tabular-nums">
              <Figure minor={current.expense} />
            </p>

            {/* THE COMPARISON. Words first, then the number, then the sign — so
                the direction survives a monochrome print and a screen reader and
                does not depend on the colour being readable. */}
            <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-[0.9375rem] text-lime-ink">
              {change === null ? (
                <span className="text-lime-ink-deep">No earlier period to compare against.</span>
              ) : (
                <>
                  {/* The direction is a WORD, not a colour and not a sign. The
                      percentage follows it, so the sentence reads "43.4% less
                      than September 2026" rather than the "Less than 43.4%…" a
                      bare adverb produces. Colour is the third signal, never the
                      only one. */}
                  {change !== 0 && (
                    <span
                      className={cn(
                        "font-mono font-medium tabular-nums",
                        change > 0 ? "text-money-out" : "text-lime-ink-deep",
                      )}
                    >
                      {formatPercent(Math.abs(change))}
                    </span>
                  )}
                  <span className="text-lime-ink">
                    {change === 0 ? "Level with" : change > 0 ? "more than" : "less than"}
                  </span>
                  <span className="text-lime-ink">
                    {previous === null
                      ? "the period before."
                      : `${monthLabel(previous.period)}, which was ${formatMajor(previous.expense)}.`}
                  </span>
                </>
              )}
            </p>
          </>
        )}
      </div>

      {/* THE TOP CATEGORY — the sentence that makes the headline checkable, since
          it attaches the number to something the reader can open and read.

          IT STATES ITS OWN WINDOW, and it has to. The figure above is one period,
          the current one, which on the sixth of a month is six days; the category
          breakdown covers the whole window the response asked for. An earlier
          version of this sentence said the top category was "of the amount above",
          which on the demo read "5,945.00 EUR of the 2,128.60 EUR above" — a
          category that costs nearly three times the period it was said to be part
          of. The figure was right and the sentence was lying. The two are now
          reported side by side with their windows on them and no arithmetic
          between them. */}
      {top !== null && !loading && error === null && (
        <p className="mt-6 border-t border-lime-ink/25 pt-4 text-[0.8125rem] leading-relaxed text-lime-ink">
          <span className="font-medium">Largest category</span> across the months
          shown: <span className="font-medium">{top.category_name}</span>,{" "}
          <span className="font-mono tabular-nums">{formatMajor(Math.abs(top.amount))}</span>
          .{" "}
          <Link
            to="/finance/transactions"
            className="underline decoration-current/40 underline-offset-4 transition-colors hover:decoration-current"
          >
            Every transaction
          </Link>
        </p>
      )}
    </section>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   Spending per month. Its own panel, on warm white.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Six months of spending, one bar each.
 *
 * IT IS NOT IN THE LIME PANEL, which is a correction the brief asks for twice
 * over: the leading panel is the loud region, not the tall one, and a six-bar chart
 * pushed it to about 640px. See the note on `SpendPanel`.
 *
 * WARM WHITE AND NOT CYAN. The rule is that the second slot's cyan fill cannot sit
 * next to another cyan fill, and the category breakdown directly below this one is
 * cyan. Two saturated panels stacked in one column is a stripe, not a hierarchy, so
 * this panel is the plain `--panel` and the cyan stays unique in its column. Lime
 * remains the only saturated field in the wide column.
 *
 * `WHEN`, not `HOW MUCH`. The amount above is the current month; this is the shape
 * of the six months around it, which is the question a person asks after seeing a
 * figure that surprised them.
 *
 * The bars are `div`s with an explicit height rather than an SVG or a canvas: six
 * rectangles whose heights are proportional, and there is no chart library in this
 * project to justify anything more.
 */
const MonthsPanel: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  const { cashflow, loading, error, reload } = overview;
  const months = monthAxis(cashflow, overview.netWorth);
  const peak = Math.max(1, ...cashflow.map((bucket) => bucket.expense));
  const gaps = months.filter((month) => !cashflow.some((entry) => entry.period === month));

  return (
    <section aria-labelledby="months-heading" className="flex flex-col rounded-md bg-panel p-6">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
        <h2 id="months-heading" className="text-[1.0625rem] font-semibold tracking-[-0.01em]">
          {/* NOT "Spent". The leading panel is already `Spent · EUR`, and two panels
              on one screen both called "Spent" is a reader doing the work of working
              out which is which. This one answers WHEN rather than HOW MUCH, so it
              is named for that. */}
          Month by month <span className="text-ink-quiet">· EUR out</span>
        </h2>
        <p className="eyebrow text-ink-quiet">Six months</p>
      </div>

      {loading ? (
        <p role="status" aria-label="Reading spending per month" aria-busy="true" className="mt-4 text-[0.9375rem] text-ink-quiet">
          Reading the ledger…
        </p>
      ) : error !== null ? (
        <div className="mt-3" role="alert">
          <p className="text-[0.8125rem] leading-relaxed text-money-out">{error}</p>
          <button type="button" onClick={reload} className="btn btn-quiet mt-3">
            Try again
          </button>
        </div>
      ) : cashflow.length === 0 ? (
        <p className="mt-4 max-w-[46ch] text-[0.875rem] leading-relaxed text-ink-quiet">
          No month in this window has any movement on it. There is nothing here to
          compare the current month against, and an empty chart would suggest
          otherwise.
        </p>
      ) : (
        <>
          {/* `items-start`, not `items-end`, and that is the whole fix for the baseline.
               A bottom-aligned list aligns the COLUMN BOXES, so a column whose
               caption wraps to two lines is taller than its neighbours and its strip
               gets lifted off the shared baseline — which is exactly how the empty
               month came to read as a stub sitting above the other five. Top-aligned,
               every strip starts at the same y, every strip is the same height, and
               every baseline is identical however many lines a caption runs to. The
               alignment is then structural rather than a number tuned to one font at
               one size. */}
          <ul className="mt-5 flex items-start gap-2" aria-label="Spending per month">
            {months.map((month) => {
              const monthName = monthLabel(month);
              const bucket = cashflow.find((entry) => entry.period === month);
              /* The empty state is named on the COLUMN, via an `aria-label` on the
                 list item, rather than in a hidden span inside it: the column is
                 60px wide and a sentence laid out inside it overflows. An
                 aria-label takes up no space at all. */
              return (
                <li
                  key={month}
                  className="flex flex-1 flex-col items-center gap-2"
                  aria-label={
                    bucket === undefined
                      ? `${monthName}, no movement`
                      : `${monthName}, ${formatMajor(bucket.expense)} out`
                  }
                >
                  <div className="flex h-20 w-full items-end" aria-hidden="true">
                    {bucket === undefined ? (
                      /* A MONTH WITH NOTHING IN IT IS A FLOOR MARKER, NOT A SHORT
                         BAR. Two earlier versions got this wrong. The first drew
                         nothing at all, which left an unlabelled hole that read as
                         a rendering fault. The second drew a dashed stub sitting
                         15px above the other bars' baseline — because the "none"
                         caption made this one `<li>` taller than its neighbours and
                         the list is bottom-aligned — and a mark that looks like a
                         bar but is not one is worse than a hole.

                         What is drawn here is the axis floor itself, in the same ink
                         as every other mark and at a third of its strength, with
                         the word beneath. It says where zero is and that nothing
                         reached it. Upstream omits a period with no movement rather
                         than emitting a zero, so any height here — including a
                         3px one — would put a bar on the page for a month in which
                         nothing happened and a reader would count it. */
                      <span className="block h-0.5 w-full bg-ink/30" />
                    ) : (
                      <div
                        /* INK. A bar is a mark on the panel and the mark in this
                           world is ink; the previous dark teal sat close enough to
                           the fill to read as part of it. */
                        className="w-full bg-ink"
                        style={{
                          /* Floor of 3px so a small month is still visibly a
                           month rather than rounding away to nothing; the exact
                           figure is in the aria-label above. */
                          height: `${Math.max(3, (bucket.expense / peak) * 100)}%`,
                        }}
                      />
                    )}
                  </div>
                  {/* The caption may be one line or two; `items-start` above means that no longer
                      touches the bars, so it is free to be whatever height it needs. */}
                  <span
                    aria-hidden="true"
                    className={cn(
                      "flex flex-col items-center text-[0.6875rem] font-medium tracking-[0.04em]",
                      bucket === undefined ? "text-ink-quiet" : "text-ink-quiet",
                    )}
                  >
                    {monthTick(bucket?.period ?? month)}
                    {bucket === undefined && (
                      <span className="text-[0.625rem] tracking-normal normal-case">
                        none
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>

          {/* THE GAP EXPLANATION, ONE CLICK AWAY. The caveat itself is the visible
              line — a reader sees a floor marker with no bar on it and is told
              immediately that it means absence rather than zero. */}
          {gaps.length > 0 && (
            <Disclosure
              className="mt-3"
              summary={`${gaps.map(monthLabel).join(" and ")} ${
                gaps.length === 1 ? "has" : "have"
              } no bar because nothing posted in ${
                gaps.length === 1 ? "it" : "them"
              }, not because ${
                gaps.length === 1 ? "it" : "they"
              } cost nothing.`}
            >
              <p>
                A month with nothing posted in it is left empty on purpose.
                Cashflow reports no bucket rather than a zero, so a bar at zero
                height would look like a month that happened to cost nothing — and
                a reader counting six bars would count one that does not exist. The
                floor marker is where zero is; the word beneath it is the finding.
              </p>
            </Disclosure>
          )}
        </>
      )}
    </section>
  );
};

/** The leading panel's empty state, and the reason it is not a zero. `0.00` spent
 * would be a claim — it would say the month cost nothing, when the truth is that
 * nothing has been recorded. */
const EmptySpend: React.FC = () => (
  <div>
    <p className="text-[1.375rem] leading-snug font-medium text-ink">
      Nothing spent in this window yet.
    </p>
    <p className="mt-2 max-w-[46ch] text-[0.8125rem] leading-relaxed text-lime-ink-deep">
      No transaction has posted against an account in this period. Money moved
      between your own accounts is not spending and is not counted here. A month
      with no postings is a gap, not a zero.
    </p>
  </div>
);

/** A percentage to one decimal, from a float, for the one place a percentage is
 * shown. Every AMOUNT on this page is integer minor units — a percentage is a
 * ratio between two of them and is formatted here rather than passed through the
 * money helpers, which is why this function exists at all. */
const formatPercent = (value: number): string => {
  const rounded = Math.round(value * 10) / 10;
  const text = rounded.toFixed(1);
  return `${text.endsWith(".0") ? text.slice(0, -2) : text}%`;
};

/* ═══════════════════════════════════════════════════════════════════════════
   The review stub — the one perforated edge in this app.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════════════════
   The review stub — the one perforated edge in this app.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * A tear-off stub, and the only place in this app where a panel's FILL is its
 * state.
 *
 * Cyan when the queue is clear, lime when there is something to clear. The
 * perforation is on the panel's mask and never animates, so the shape identifies
 * the panel before the colour does — which is the brief's requirement, and the
 * reason the mask is a mask rather than a coloured border-image.
 *
 * The fill change is the page's ONE authored motion moment. Under
 * `prefers-reduced-motion` the animation is removed entirely (see index.css), so
 * the colour swaps instantly and the perforation does not move.
 *
 * Three carriers for the state, so colour is never the only one: the fill, the
 * word ("Clear" / "Waiting"), and the count itself.
 */
const ReviewStub: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  /* Three states, not two. `null` means the queue has not been established — the
     read is in flight or it failed — and reporting that as zero would tell a
     reader their money is tidy when nobody has looked. Only a positive count
     earns lime. */
  const known = overview.reviewOpen !== null;
  const waiting = (overview.reviewOpen ?? 0) > 0;

  const { nextImport } = overview;

  /* The key changes with the state so the animation replays on every transition
     rather than only on the first paint. The same key on two renders of the same
     state means no animation at all, which is what you want: a page that
     re-renders should not keep re-travelling. */
  const state = waiting ? "lime" : "cyan";

  return (
    <section
      aria-labelledby="review-heading"
      className={cn(
        "stub-edge rounded-b-md p-6 pt-8 text-ink",
        waiting ? "bg-lime" : "bg-cyan",
        "stub-fill-travel",
      )}
      style={
        {
          "--stub-fill-from": waiting ? "var(--cyan)" : "var(--lime)",
          "--stub-fill-to": waiting ? "var(--lime)" : "var(--cyan)",
        } as React.CSSProperties
      }
      /* The key is the whole mechanism; see above. */
      key={`stub-${state}-${overview.reviewOpen ?? "unknown"}`}
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="review-heading" className="text-[1.0625rem] font-semibold tracking-[-0.01em]">
          Review queue
        </h2>
        <p
          className={cn(
            "eyebrow",
            waiting ? "text-lime-ink-deep" : "text-cyan-ink-deep",
          )}
        >
          {!known ? "Unknown" : waiting ? "Waiting" : "Clear"}
        </p>
      </div>

      <p className="mt-3 font-mono text-[2rem] leading-none font-medium tabular-nums">
        {known ? overview.reviewOpen : "—"}
        <span className="ml-2 align-baseline text-[0.9375rem] font-normal tracking-[-0.01em]">
          {!known
            ? "queue could not be read"
            : waiting
              ? overview.reviewOpen === 1
                ? "transaction needs a category"
                : "transactions need a category"
              : "nothing waiting"}
        </span>
      </p>

      <p
        className={cn(
          "mt-2 text-[0.8125rem] leading-relaxed",
          waiting ? "text-lime-ink" : "text-cyan-ink",
        )}
      >
        {!known
          ? "Nobody has looked at the queue yet, so this panel is not claiming it is empty."
          : waiting
            ? "Uncategorised money out waits here until you say what it was. Nothing is guessed past the point of being wrong."
            : "Every transaction that came in has a category, so there is no decision outstanding."}
      </p>

      {/* The second field of the slip, and the reason the stub is perforated at
          all: this is the half the reader keeps and the half they tear off. */}
      <p className="mt-5 border-t border-current/20 pt-4 text-[0.8125rem] leading-relaxed text-current">
        <span className="font-medium">Next import </span>
        {nextImport === null ? (
          <span className="text-cyan-ink">
            {" "}
            is not scheduled. LifeOS imports a statement when you hand it one — there
            is no calendar behind this panel.
          </span>
        ) : (
          <span className={waiting ? "text-lime-ink" : "text-cyan-ink"}>
            {" "}
            {formatDay(nextImport.dueDate)},{" "}
            {nextImport.inDays === 0
              ? "today"
              : nextImport.inDays === 1
                ? "tomorrow"
                : `in ${nextImport.inDays} days`}.
          </span>
        )}
      </p>
    </section>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   The net-worth series. Not a bankgier, and deliberately quiet.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Six months of net worth, directly beneath the figure it belongs to.
 *
 * The honest risk in the surface brief is exactly here: a net-worth SERIES has no
 * counterpart on a printed payment slip, and forcing one turns the grammar into a
 * costume. So the mitigation is what is built here — the series is a plain panel
 * on the ground, in no brand fill, one step down from the figure above it. It
 * reports; it does not perform.
 *
 * TWO THINGS ON THIS PANEL ARE DECISIONS, AND BOTH ARE STATED ON IT
 * ------------------------------------------------------------
 * One is the axis, and the reasoning is written out at `axisDomain`. The short
 * version: an auto-scaled axis turns a salary into a cliff and a quarter's
 * spending into a plateau, which is a lie about the data — and on this product the
 * one failure that matters is a chart that misrepresents a ledger.
 *
 * The other is the flat stretch. Upstream emits a point for every day in the
 * range, so a week with no postings arrives as a run of identical values, and
 * drawing that run as a solid line asserts "net worth was constant" — which the
 * ledger does not claim. Nothing posted. So held stretches are drawn dashed and
 * labelled, which says "carried forward from the last posting" and lets the
 * observed line be the only solid one. See `splitSegments`.
 */
const NetWorthSeries: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  const { netWorth, loading, error, reload, empty } = overview;
  /* An all-zero series from an empty ledger is not a flat line at zero, it is the
     absence of the thing the panel draws. The account list decides, not the
     numbers — see `isEmpty`. */
  const points = empty ? EMPTY_SERIES : netWorth;

  const scale = React.useMemo(() => {
    if (points.length === 0) {
      return null;
    }
    const values = points.map((point) => point.net_worth);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const domain = axisDomain(min, max);
    const { floor, ceil } = domain;
    const span = ceil - floor || 1;
    const step = points.length > 1 ? 100 / (points.length - 1) : 100;

    /* Each day gets a point. A day is HELD when its value is identical to the
       day's before it — no posting landed, so the running total was carried
       forward rather than observed. No ledger entry moves exactly zero cents, so
       an identical pair is unambiguously a quiet stretch and not a coincidence.
       That is derivable from the real endpoint too, not just the demo. */
    const coords = values.map((value, index) => ({
      x: index * step,
      y: 100 - ((value - floor) / span) * 100,
    }));

    const observed: string[] = [];
    const held: string[] = [];
    for (let i = 0; i < coords.length - 1; i += 1) {
      const a = coords[i]!;
      const b = coords[i + 1]!;
      const segment = `M${a.x.toFixed(3)},${a.y.toFixed(3)}L${b.x.toFixed(3)},${b.y.toFixed(3)}`;
      if (values[i] === values[i + 1]) {
        held.push(segment);
      } else {
        observed.push(segment);
      }
    }

    /* The area fill is GONE, and this is the decision rather than an omission.

       It was tried twice. Once with the axis scaled to the data range, where it
       was a thin band under a line that filled the plot. Once with the axis
       widened to stop the line filling the plot, where it became a 40%-height
       grey slab standing on the axis floor with nothing between it and the data —
       because a fill is bounded by the plot floor, and a floor chosen to stop a
       salary looking like a cliff is necessarily far below where the value ever
       sits.

       The alternative was to bound the fill to the data instead of the axis, which
       means inventing a second reference line that is neither zero nor the axis
       and then explaining it. That trades one thing to get wrong for two.

       A line against a stated axis has no slab to get wrong. The panel already
       carries the figure, the axis bounds, the dashed held segments and the
       reasoning for all three; the fill was a fourth visual layer that could only
       ever be decoration. */
    return {
      observed: observed.join(""),
      held: held.join(""),
      heldDays: held.length,
      domain,
      min,
      max,
    };
  }, [points]);

  const first = points.at(0);
  const last = points.at(-1);

  return (
    <section aria-labelledby="series-heading" className="flex flex-col rounded-md bg-panel p-6">
      {/* `sm:flex-row` so the heading and its link stack at 390px rather than the
          link squeezing the heading onto two lines. The heading is the panel's
          voice and the link is a footnote on it; the footnote loses that argument
          at every width. */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
        <h2 id="series-heading" className="text-[1.0625rem] font-semibold tracking-[-0.01em]">
          Net worth over six months
        </h2>
        <Link
          to="/finance/accounts"
          className="inline-flex w-fit shrink-0 items-center gap-1 text-[0.8125rem] text-ink-quiet underline decoration-current/40 underline-offset-4 transition-colors hover:text-ink hover:decoration-current"
        >
          The accounts behind it
          <ArrowRight className="size-3.5" />
        </Link>
      </div>

      {loading ? (
        <p role="status" aria-label="Reading net worth series" aria-busy="true" className="mt-4 text-[0.9375rem] text-ink-quiet">
          Reading the ledger…
        </p>
      ) : error !== null ? (
        <div className="mt-3" role="alert">
          <p className="text-[0.8125rem] leading-relaxed text-money-out">{error}</p>
          <button type="button" onClick={reload} className="btn btn-quiet mt-3">
            Try again
          </button>
        </div>
      ) : points.length === 0 || scale === null ? (
        <p className="mt-4 max-w-[54ch] text-[0.875rem] leading-relaxed text-ink-quiet">
          There is no series to draw, because the ledger has no balances in it yet.
          A line here would be a picture of nothing.
        </p>
      ) : (
        <>
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            /* The plot is 144/176px rather than 176/208px. With no fill under it, the axis
               rule leaves the ink in a band about a quarter of the height, and at
               208px that band was 56px of line in a 208px box — which reads as a
               large empty panel with a line in it. The extra 48px bought nothing
               the sawtooth needed: the payday step is still 25px of rise. */
            className="mt-4 h-36 w-full sm:h-44"
            role="img"
            aria-label={`Net worth from ${formatDay(first?.date ?? "")} to ${formatDay(last?.date ?? "")}, lowest ${formatMajor(scale.min)}, highest ${formatMajor(scale.max)}. The axis runs from ${formatMajor(scale.domain.floor)} to ${formatMajor(scale.domain.ceil)} and does not start at zero.`}
          >
            {/* No fill, no gradient, no `<defs>`. Two paths and nothing else — see
                the note on the `scale` memo for why the area under the line was
                removed rather than re-anchored. */}
            {scale.held !== "" && (
              /* Drawn BEFORE the observed line so the solid stroke caps any dash
                 that ends where movement resumes, and the joins read as one line
                 that changed certainty rather than as two lines. */
              <path
                d={scale.held}
                fill="none"
                stroke="var(--ink)"
                strokeWidth={1.75}
                strokeDasharray="2 3"
                strokeLinecap="butt"
                strokeOpacity={0.5}
                vectorEffect="non-scaling-stroke"
              />
            )}
            {scale.observed !== "" && (
              <path
                d={scale.observed}
                fill="none"
                stroke="var(--ink)"
                strokeWidth={1.75}
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>

          {/* THE AXIS BOUNDS, and nothing else. These are the bounds the plot was
              drawn against — not the data's own extremes, because a widened bound
              presented as a reading would be a number the ledger never reported.
              The data's low and high are inside the disclosure below, labelled as
              the data's. */}
          <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 font-mono text-[0.6875rem] tabular-nums text-ink-quiet">
            <span>{formatMajor(scale.domain.floor)}</span>
            <span>{formatMajor(scale.domain.ceil)}</span>
          </div>

          {/* THE REASONING, ONE CLICK AWAY.
              This is the most honest thing on the page and it stays whole — the
              axis it chose, the option it rejected and why, and the reason a
              straight stretch is dashed. What changed is that it is no longer in
              the reading path. Printed flat it was five lines of methodology under
              a chart, on a page someone opens to read a number; the sentences were
              right and the placement was not.

              The visible line is not "More". It is the caveat itself, so the
              disclosure is findable by the question it answers — a reader who
              thinks the axis is lying can see that it knows it is not anchored at
              zero without opening anything. */}
          <Disclosure
            className="mt-2"
            summary={`This axis starts at ${formatMajor(scale.domain.floor)}, not zero — why, and what the dashed stretches mean.`}
          >
            <p>
              The axis runs {formatMajor(scale.domain.floor)} to{" "}
              {formatMajor(scale.domain.ceil)} and does not start at zero. It has
              to, because zero would squeeze six months of movement into the
              bottom{" "}
              {Math.round(rangeShare(scale.min, scale.max, scale.domain) * 100)}% of
              the height. In that window net worth ran from {formatMajor(scale.min)}{" "}
              to {formatMajor(scale.max)}
              {scale.heldDays > 0 && (
                <>
                  , and {scale.heldDays}{" "}
                  {scale.heldDays === 1 ? "day" : "days"} passed with no posting at
                  all. Those stretches are dashed, because a solid line across them
                  would claim net worth held still, and the truth is that nothing
                  was posted
                </>
              )}
              .
            </p>
          </Disclosure>
        </>
      )}
    </section>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   Categories. Ranked bars, on cyan.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Where the money went, one row per category.
 *
 * CYAN IS THE SECOND SLOT'S FILL, and it is not decoration: the page runs on two
 * brand colours at equal rank, and when spending moved into the lead the lead took
 * the lime with it. Something has to hold the other fill, and the second panel in
 * the reading order is the right place — lime answers HOW MUCH, cyan answers WHERE
 * IT WENT, and the two are different questions rather than two sizes of one.
 *
 * It is not lime, and that is the rule worth keeping: lime is the one loud region
 * on this page, and a second lime field would be two headlines. Cyan is the same
 * area and a step quieter, which is exactly the relationship.
 *
 * Ranked by magnitude, largest spend first — the order the endpoint already
 * returns. Every row carries its own amount in the mono, right-aligned and
 * tabular, so the column is scannable straight down and the bar is a second read
 * rather than the only one.
 *
 * The one-transaction category is the case this panel is built to survive: it
 * gets a real row, a real bar at whatever fraction of the peak it happens to be
 * (floored so it is still visible), and its amount spelled out. It is never
 * hidden as an outlier and never rounded up to share the leader's width.
 */
const CategoriesPanel: React.FC<{ overview: ReturnType<typeof useOverview> }> = ({ overview }) => {
  const { spendByCategory, loading, error, reload } = overview;

  const rows = React.useMemo(() => {
    const expenses = spendByCategory.filter((point) => point.amount < 0);
    const peak = Math.max(1, ...expenses.map((point) => Math.abs(point.amount)));
    return expenses.map((point) => ({
      point,
      width: `${Math.max(2, (Math.abs(point.amount) / peak) * 100)}%`,
    }));
  }, [spendByCategory]);

  return (
    <section aria-labelledby="categories-heading" className="flex flex-col rounded-md bg-cyan p-6 text-ink">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
        <h2 id="categories-heading" className="text-[1.0625rem] font-semibold tracking-[-0.01em]">
          Where it went <span className="text-cyan-ink-deep">· EUR out</span>
        </h2>
        <Link
          to="/finance/transactions"
          className="inline-flex w-fit shrink-0 items-center gap-1 text-[0.8125rem] text-cyan-ink-deep underline decoration-current/40 underline-offset-4 transition-colors hover:text-ink hover:decoration-current"
        >
          Every transaction
          <ArrowRight className="size-3.5" />
        </Link>
      </div>

      {loading ? (
        <p role="status" aria-label="Reading categories" aria-busy="true" className="mt-4 text-[0.9375rem] text-cyan-ink">
          Reading the ledger…
        </p>
      ) : error !== null ? (
        <div className="mt-3" role="alert">
          <p className="text-[0.8125rem] leading-relaxed text-money-out">{error}</p>
          <button type="button" onClick={reload} className="btn mt-3 bg-ink text-cyan hover:bg-ink/85">
            Try again
          </button>
        </div>
      ) : rows.length === 0 ? (
        <p className="mt-4 max-w-[46ch] text-[0.875rem] leading-relaxed text-cyan-ink-deep">
          Nothing has been spent in this window. Money moved between your own
          accounts does not count as spending, and it is not reported here as if it
          were.
        </p>
      ) : (
        <>
          <ul className="mt-4 flex flex-col gap-3.5">
            {rows.slice(0, VISIBLE_CATEGORIES).map(({ point, width }) => (
              <li
                key={point.category_id}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1"
              >
                <span className="truncate text-[0.875rem]">{point.category_name}</span>
                {/* Unsigned. The heading carries the direction for the whole
                    column; a minus on all eight rows repeats it eight times and
                    says nothing. */}
                <span className="font-mono text-[0.875rem] tabular-nums">
                  <Figure minor={Math.abs(point.amount)} />
                </span>
                {/* The bar sits on its own row so a long category name cannot push
                    it out of alignment with the figures above it. INK, on the same
                    rule as every other mark in this world. */}
                <div className="col-span-2 h-1.5 w-full bg-ink/10">
                  <div className="h-full bg-ink" style={{ width }} />
                </div>
              </li>
            ))}
          </ul>
          {/* The remainder is stated, not hidden. A category list that silently
              stops at eight looks like eight is all there is, and the whole point
              of this page is that a number is only worth reading if you know what
              it left out. */}
          {rows.length > VISIBLE_CATEGORIES && (
            <p className="mt-4 border-t border-ink/15 pt-3 text-[0.75rem] text-cyan-ink-deep">
              {rows.length - VISIBLE_CATEGORIES} smaller{" "}
              {rows.length - VISIBLE_CATEGORIES === 1 ? "category" : "categories"} not
              shown —{" "}
              <Link
                to="/finance/transactions"
                className="underline decoration-current/40 underline-offset-4 transition-colors hover:text-ink hover:decoration-current"
              >
                every transaction
              </Link>{" "}
              has the rest.
            </p>
          )}
        </>
      )}
    </section>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   Controls.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * The demo control.
 *
 * Deliberately the loudest control on the page and deliberately NOT styled like
 * the others: it is not a preference, it is a switch between the user's ledger
 * and invented numbers, and a reader has to be able to tell which one they are
 * looking at without reading a word. Lime when it is on, because lime is what
 * marks invented in this app, and the label says `DEMO` in capitals.
 *
 * Turning it off clears `?demo=1` from the URL, so the address bar and the screen
 * cannot disagree about what a reload will show.
 */
/**
 * The demo control.
 *
 * OFF, it is a quiet button that says what it does. ON, it disappears entirely —
 * the banner above owns the state and offers the way out, so leaving a second
 * control here would be saying the same thing again in a smaller voice.
 *
 * The switch keeps `aria-checked` even when it is only ever rendered in the false
 * state: the role is what a screen reader announces when focus lands here, and
 * "switch, off" is more use than "button" for a control that flips a mode.
 */
const DemoToggle: React.FC<{ demo: boolean; onChange: (on: boolean) => void }> = ({
  demo,
  onChange,
}) =>
  demo ? null : (
    <button
      type="button"
      role="switch"
      aria-checked={false}
      onClick={() => onChange(true)}
      className="rounded-md bg-panel px-3.5 py-2 text-[0.8125rem] font-medium text-ink transition-colors hover:bg-ground"
    >
      Show demo data
    </button>
  );

/** The banner. Full width, ink, above the grid — the one place a third colour
 * appears on this page, because it is a notice ABOUT the page rather than
 * something the page is reporting. */
const DemoBanner: React.FC<{ onExit: () => void }> = ({ onExit }) => (
  <div className="mb-6 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 rounded-md bg-ink px-5 py-3">
    <p className="text-[0.875rem] font-medium text-lime">
      Demo data. Every figure below is invented, and none of it is in your ledger.
    </p>
    <button
      type="button"
      onClick={onExit}
      className="rounded px-2 py-1 text-[0.8125rem] font-medium text-panel underline decoration-current/40 underline-offset-4 transition-colors hover:decoration-current"
    >
      Show my ledger
    </button>
  </div>
);

/* ═══════════════════════════════════════════════════════════════════════════
   Formatting and small pure helpers.
   ═══════════════════════════════════════════════════════════════════════════ */

/**
 * One amount, formatted by `@/lib/money` and nothing else.
 *
 * THE PAGE'S UNIT CONVENTION, and it is one convention everywhere: **no figure on
 * this page carries a currency code.** An earlier draft mixed three — `41,005.64
 * EUR` on the hero, a bare `1,288.00` in the spend panel, and a bare figure in the
 * category list — which is three answers to "what unit is this?" for the reader to
 * reconcile, and the only place the answer was wrong by omission was the list.
 *
 * The unit is stated ONCE per panel instead, in the panel's own label, because a
 * panel is the thing that has a unit and a row inside it does not. `EUR` is
 * EUR-base only by construction (ARCHITECTURE.md §6), so it is a fact about every
 * number here rather than a fact about any of them.
 *
 * The sign follows `signed`. A balance and a magnitude do not carry one — a
 * negative net worth keeps its sign because it is genuinely below zero, and the
 * hero figure is never prefixed with `+` — but the delta does, because there the
 * direction IS the content. The category rows are unsigned: their heading already
 * says the money went out, and eight minus signs in a column repeat something the
 * reader has not forgotten.
 */
const Figure: React.FC<{ minor: number; signed?: boolean }> = ({ minor, signed = false }) => {
  const { sign, digits } = amountParts(minor, "EUR");
  const shown = signed && minor > 0 ? `+${digits}` : minor < 0 ? `${sign}${digits}` : digits;
  return <>{shown}</>;
};

/** A bare amount for a tooltip or an axis, where there is no space for a sign and
 * no row to align. Still integer arithmetic, still `amountParts`. */
const formatMajor = (minor: number): string => {
  const { digits } = amountParts(Math.abs(minor), "EUR");
  return `${minor < 0 ? "−" : ""}${digits} EUR`;
};

/** `YYYY-MM-DD` → `5 October`, from the browser's own clock.
 *
 * Built from the parsed parts rather than through `Date`'s string parsing, which
 * treats `YYYY-MM-DD` as UTC and can therefore land on the previous day for
 * anyone west of Greenwich — and a net-worth series whose last point is stamped
 * with yesterday's date is wrong in the one place a reader is most likely to
 * check it. */
const formatDay = (iso: string): string => {
  const [year, month, day] = iso.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) {
    return iso;
  }
  const name = MONTHS_SHORT[month - 1] ?? "";
  return `${day} ${name} ${year}`;
};

/** `2026-10` → `October`, for a month bucket's caption. */
const monthLabel = (period: string): string => {
  const [year, month] = period.split("-").map(Number);
  if (year === undefined || month === undefined) {
    return period;
  }
  const full = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  return `${full[month - 1] ?? period} ${year}`;
};

/** `2026-10` → `Oct`, for a six-column axis where `October` will not fit. */
const monthTick = (period: string): string => {
  const month = Number(period.slice(5, 7));
  return MONTHS_SHORT[month - 1] ?? period;
};

/* The net-worth y-axis is a decision, and it lives in its own module. See
 * `./axis.ts` for why a deliberately wide truncated scale is the honest answer
 * here, what the numbers were before, and what this axis looked like when it
 * was simply the data's own range. */

/**
 * The month axis, reconstructed rather than taken from the response.
 *
 * Cashflow omits a period with no movement, so the response cannot say how many
 * months it was asked about. A bar chart built straight off the response would
 * therefore draw five bars for six months and hide the gap inside whichever month
 * happened to be adjacent — which is precisely the month the reader needs to see
 * is missing.
 *
 * With the demo on, the demo supplies its own full axis for the same reason.
 */
const monthAxis = (cashflow: CashflowBucket[], netWorth: NetWorthPoint[]): string[] => {
  const last = netWorth.at(-1)?.date;
  if (last === undefined) {
    /* No series to anchor the window on. Whatever came back is all there is. */
    return cashflow.map((bucket) => bucket.period);
  }
  const [year, month] = last.slice(0, 7).split("-").map(Number);
  if (year === undefined || month === undefined) {
    return cashflow.map((bucket) => bucket.period);
  }

  const months: string[] = [];
  for (let back = 5; back >= 0; back -= 1) {
    /* `month` off a `YYYY-MM` split is 1-based, and the arithmetic below is
     * 0-based, so the conversion happens once here. Getting it wrong puts the
     * axis one month ahead of the data — which moves the gap column off the month
     * it belongs to and reports a November that has not happened. */
    const index = month - 1 - back;
    const y = year + Math.floor(index / 12);
    const m = ((index % 12) + 12) % 12;
    months.push(`${y}-${`${m + 1}`.padStart(2, "0")}`);
  }

  /* An empty response means an empty ledger, and six empty columns above a
     sentence saying so is six empty columns. One column is enough to say it. */
  return cashflow.length === 0 ? months.slice(-1) : months;
};

/**
 * The change over the last 30 days, from the same daily series.
 *
 * Takes the point nearest 30 days back rather than "the point 30 indices back",
 * because the series is daily but a request that started mid-month has a first
 * point in the middle of it, and an index would silently compare the wrong two
 * days. Null when there is no earlier point, because "unchanged" and "nothing to
 * compare" are different answers and the panel says which one it is.
 */
const periodDelta = (series: NetWorthPoint[]): number | null => {
  const latest = series.at(-1);
  if (latest === undefined) {
    return null;
  }
  const target = new Date(latest.date);
  target.setDate(target.getDate() - 30);
  const cutoff = `${target.getFullYear()}-${`${target.getMonth() + 1}`.padStart(2, "0")}-${`${target.getDate()}`.padStart(2, "0")}`;

  /* The LAST point at or before the cutoff: the balance on that day, not the
     nearest one after it. */
  let earlier: NetWorthPoint | null = null;
  for (const point of series) {
    if (point.date <= cutoff) {
      earlier = point;
    } else {
      break;
    }
  }
  if (earlier === null) {
    return null;
  }
  return latest.net_worth - earlier.net_worth;
};