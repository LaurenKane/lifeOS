/* ── Demo data ─────────────────────────────────────────────────────────────
 * INVENTED FIGURES. Not a seed script, not a fixture, and it never reaches the
 * database. This module exists for exactly one purpose: letting the type and the
 * layout be judged against numbers on screen, which is impossible on an empty
 * database.
 *
 * IT IS NEVER SUBSTITUTED SILENTLY. `use-overview.ts` only reads this after an
 * explicit opt-in — a visible control, or `?demo=1` — and when it is on, the page
 * says so in three places at once: a full-width banner, the word DEMO beside the
 * net-worth label, and the type control's own group name. Real-empty and demo
 * are two different-looking screens, not one screen with different numbers.
 *
 * WHY THIS IS A LEDGER AND NOT THREE HAND-WRITTEN ARRAYS
 * ------------------------------------------------------
 * The three endpoints are aggregates over journal lines, and each has a rule
 * that is easy to get wrong in a way that still produces plausible numbers: net
 * worth is assets PLUS liabilities (they are stored negative, so subtracting
 * again reports debt as wealth); spend-by-category excludes transfers and
 * excludes the equity contra-leg; cashflow drops any entry whose every leg is
 * an asset, because moving your own money around is not income.
 *
 * So this file authors POSTINGS and derives the responses using the same rules
 * `backend/finance/domain/services/analytics.py` applies. If the rules disagree,
 * the demo is visibly wrong rather than quietly plausible — which is the only
 * way a mock of a ledger is any use at all.
 *
 * WHY THE POSTINGS ARE GENERATED AND NOT WRITTED OUT
 * --------------------------------------------------
 * A demo pinned to literal calendar months shows nothing at all six months from
 * now, which would make it a fixture with a use-by date. So the months are
 * offsets from the current one and the whole window moves with it. The
 * irregularities are authored per month-offset rather than per date, so they
 * survive the move.
 *
 * THE AWKWARD CASES, DELIBERATELY
 * -------------------------------
 *   - A category with exactly ONE transaction (offset −3, one pharmacy visit), so
 *     a ranked bar chart has to draw a one-row bar and report a share of a total
 *     it is a rounding error of.
 *   - A MONTH WITH NO SPENDING AT ALL (offset −4): only a salary and two
 *     transfers post. Cashflow omits empty periods upstream, so that month
 *     arrives as a GAP and the chart has to leave the space empty rather than
 *     dropping a zero bar into it.
 *   - A FLAT NET-WORTH STRETCH (offset −2, days 14→23): nothing posts for ten
 *     days. A series has to draw that as flat and not as missing, and the delta
 *     has to be able to land on a day inside it without dividing by nothing.
 *   - A TRANSFER between his own accounts: in net worth, not in spending, not in
 *     income.
 *   - A CARD PAYMENT: cash leaving in cashflow (the card is a liability and so is
 *     not the other side of the cancellation) and absent from spending.
 *   - A LOAN REPAYMENT: it reduces a liability, so net worth RISES on the day it
 *     posts. A model that treats every outflow as a loss gets this backwards.
 */
import type { AccountNature, AccountType } from "@/features/finance/accounts/types";
import type { CashflowBucket, NetWorthPoint, SpendByCategoryPoint } from "./types";

/** How many months of history the demo carries.
 *
 * Six, because a chart with three points cannot show a flat stretch or a gap: both
 * need enough width to be visible at all. */
const MONTHS_OF_HISTORY = 6;

/** Month offsets used below, named so the awkward cases are findable. */
const NO_SPENDING_MONTH = 4;
const FLAT_STRETCH_MONTH = 2;
const SINGLETON_MONTH = 3;

/** Every account the demo opens, across all three natures.
 *
 * The equity account is here because it is load-bearing rather than decoration:
 * an expense is a two-legged entry and the counter-leg lives in an equity
 * account. A model of the data that omitted it would be a model where spending
 * nets to zero. */
const ACCOUNTS: ReadonlyArray<{
  name: string;
  account_type: AccountType;
  account_nature: AccountNature;
  openingMinor: number;
}> = [
  { name: "Betaalrekening", account_type: "checking", account_nature: "asset", openingMinor: 4_812_36 },
  { name: "Spaarrekening", account_type: "savings", account_nature: "asset", openingMinor: 38_500_00 },
  /* The pension pot is the reason this ledger is worth looking at twice. It is
   * also the reason the net-worth series below is not a sawtooth: it is 4× the
   * size of everything else and it barely moves, which is what a pension pot does
   * and what a current account does not. */
  { name: "Vrijepensioen", account_type: "investment", account_nature: "asset", openingMinor: 96_240_55 },
  { name: "Geld", account_type: "cash", account_nature: "asset", openingMinor: 96_00 },
  { name: "Amex", account_type: "credit_card", account_nature: "liability", openingMinor: -1_240_37 },
  { name: "Persoonlijke lening", account_type: "loan", account_nature: "liability", openingMinor: -9_600_00 },
  { name: "Uitgaven (system)", account_type: "cash", account_nature: "equity", openingMinor: 0 },
];

const NATURE = new Map<string, AccountNature>(
  ACCOUNTS.map((account) => [account.name, account.account_nature]),
);

/** One journal entry: a date, legs that sum to zero, and whether the pipeline
 * classified it as a transfer. */
type Entry = {
  /** `YYYY-MM-DD`, built by `day()`. */
  date: string;
  legs: ReadonlyArray<{ account: string; minor: number }>;
  isTransfer: boolean;
  /** The category, which upstream sits on both legs of an expense and which this
   * models on the non-equity leg alone — the same number, one fewer field. */
  category?: string;
};

/** `YYYY-MM` for the month `offset` months before the one containing `today`.
 * Negative is in the past. Built from arithmetic on the month index so a January
 * window needs no special case. */
const monthKey = (today: Date, offset: number): string => {
  const index = today.getMonth() + offset;
  const year = today.getFullYear() + Math.floor(index / 12);
  const month = ((index % 12) + 12) % 12;
  return `${year}-${`${month + 1}`.padStart(2, "0")}`;
};

/** The `day`-th of that month. Clamped to the month's length so a 31st in a
 * 30-day month posts on the 30th instead of rolling into the next one and
 * quietly changing which month a figure belongs to. */
const day = (month: string, dayOfMonth: number): string => {
  const [year = 1970, monthNumber = 1] = month.split("-").map(Number);
  const last = new Date(year, monthNumber, 0).getDate();
  const clamped = Math.min(Math.max(dayOfMonth, 1), last);
  return `${month}-${`${clamped}`.padStart(2, "0")}`;
};

/** `YYYY-MM-DD` → the next calendar day, in local time and with no timezone in
 * sight. `new Date(iso)` parses as UTC, so a `toISOString` anywhere in here
 * would move the day for anyone east or west of Greenwich — which is how an
 * off-by-one gets into a date column. */
const nextDay = (iso: string): string => {
  const [year = 1970, month = 1, dayOfMonth = 1] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, dayOfMonth + 1);
  return `${date.getFullYear()}-${`${date.getMonth() + 1}`.padStart(2, "0")}-${`${date.getDate()}`.padStart(2, "0")}`;
};

/** An expense: money out of a real account plus the equity contra-leg that makes
 * the entry balance. `minor` is the POSITIVE magnitude, because that is how a
 * person thinks about a bill; the sign is applied here so no reader of the table
 * below has to work it out. */
const expense = (
  date: string,
  account: string,
  minor: number,
  category: string,
): Entry => ({
  date,
  isTransfer: false,
  category,
  legs: [
    { account, minor: -minor },
    { account: "Uitgaven (system)", minor },
  ],
});

/** Income is the same shape with the signs the other way round. */
const income = (date: string, account: string, minor: number, category: string): Entry => ({
  date,
  isTransfer: false,
  category,
  legs: [
    { account, minor },
    { account: "Uitgaven (system)", minor: -minor },
  ],
});

/** A transfer between accounts the user owns: same amount, opposite signs. Never
 * spending, never income — only a change of where the money sits. */
const transfer = (date: string, from: string, to: string, minor: number): Entry => ({
  date,
  isTransfer: true,
  legs: [
    { account: from, minor: -minor },
    { account: to, minor },
  ],
});

/** A loan repayment: cash out of an asset, and the liability balance shrinks.
 * Net worth RISES on the day this posts, which is the whole reason a ledger is
 * worth more than a spending app. */
const repayment = (date: string, minor: number): Entry => ({
  date,
  isTransfer: false,
  legs: [
    { account: "Betaalrekening", minor: -minor },
    { account: "Persoonlijke lening", minor },
  ],
});

/** Six months of one person's money, authored as postings.
 *
 * Every recurring bill has a real amount and varies a little month to month,
 * because round numbers are exactly what makes a chart look fake. The
 * irregularities are attached to month OFFSETS rather than dates, so they move
 * with the window instead of expiring. */
const ledger = (months: readonly string[]): Entry[] => {
  const entries: Entry[] = [];

  /* Opening balances, dated the day before the window opens. Without them the
   * first plotted day would start at zero and the first month would read as a
   * gain, which is a lie about a month in which nothing happened. */
  const openingDay = monthBefore(months[0] ?? monthKey(new Date(), 0));
  for (const account of ACCOUNTS) {
    entries.push({
      date: openingDay,
      isTransfer: false,
      legs: [{ account: account.name, minor: account.openingMinor }],
    });
  }

  months.forEach((month, index) => {
    const back = index - (months.length - 1);

    /* ── THE MONTH WITH NO SPENDING ─────────────────────────────────────────
     * Two transfers and nothing else — no salary, no bills, no card. Both legs of
     * both entries are his own accounts, so cashflow drops them (money
     * rearranging inside his own accounts is not income) and spend-by-category
     * drops them (saving is not spending). The month therefore produces NO bucket
     * at all, which is the case that breaks any chart assuming one bucket per
     * month: it arrives as a gap, and the bars either side of it are not
     * neighbours.
     *
     * It is also the month the delta against 30 days lands in, so the figure
     * either side of it has to be right. */
    if (back === -NO_SPENDING_MONTH) {
      entries.push(transfer(day(month, 25), "Betaalrekening", "Spaarrekening", 75_000));
      entries.push(transfer(day(month, 27), "Spaarrekening", "Vrijepensioen", 60_000));
      return;
    }

    /* The salary lands on the 25th, the rent on the 1st. That ordering is the whole
       shape of the series and it is the ordinary one: a month spends itself down
       and then gets topped back up, so the line steps up once and eases down for
       the three weeks after, rather than spiking on the 1st.

       The second thing that matters is that NOTHING here is large relative to the
       balance. An earlier draft had a €3,485 salary against a €38k net worth —
       one posting, 9% of the whole, drawn as a cliff. Real balances are larger
       than six months of movements by two orders of magnitude, and the series only
       looks like a person's money when that is true of it. */
    const pay = back === -3 || back === 0 ? 3_485_42 : 3_314_58;
    entries.push(income(day(month, 25), "Betaalrekening", pay, "Salaris"));

    /* Bills spread across the month, each on its own day so the draw-down is a
       slope rather than a staircase, and enough of them that no two consecutive
       days are both silent.

       That last part is what makes the series read as a person's money. An
       earlier draft posted twelve times in thirty days, which left two- and
       three-day flat runs all the way across the chart and turned a real
       household into a set of steps. A household shops most days, buys coffee
       most days, and pays a dozen bills; that frequency IS the texture, and
       without it the line looks like a quarterly report rather than a ledger.

       Amounts vary month to month, because identical recurring amounts are how a
       chart looks fake. */
    const bills: ReadonlyArray<readonly [number, number, string, string]> = [
      [1, 118_900, "Betaalrekening", "Huur"],
      [2, 74_900, "Betaalrekening", "Belastingen"],
      [3, 2_480, "Betaalrekening", "Supermarkt"],
      [4, 9_900, "Betaalrekening", "Abonnementen"],
      [5, 1_285, "Betaalrekening", "Koffie"],
      [6, 3_240 + index * 431, "Betaalrekening", "Supermarkt"],
      [7, 2_190 + index * 311, "Betaalrekening", "Supermarkt"],
      [8, 4_120 + index * 137, "Betaalrekening", "Vervoer"],
      [9, 1_240, "Betaalrekening", "Supermarkt"],
      [10, 2_750, "Betaalrekening", "Supermarkt"],
      [11, 1_310, "Betaalrekening", "Koffie"],
      [12, 3_890, "Betaalrekening", "Supermarkt"],
      [13, 2_060, "Betaalrekening", "Supermarkt"],
      [14, 6_000, "Betaalrekening", "Verzekeringen"],
      [15, 2_940, "Betaalrekening", "Supermarkt"],
      [16, 1_255, "Betaalrekening", "Koffie"],
      [17, 3_499 + index * 90, "Betaalrekening", "Supermarkt"],
      [18, 2_310, "Betaalrekening", "Supermarkt"],
      [19, 2_650, "Betaalrekening", "Koffie"],
      [20, 4_180, "Betaalrekening", "Supermarkt"],
      [21, 5_500, "Betaalrekening", "Uitgaven kinderen"],
      [22, 2_070, "Betaalrekening", "Supermarkt"],
      [23, 3_890, "Betaalrekening", "Supermarkt"],
      [24, 1_290, "Betaalrekening", "Koffie"],
      [25, 2_840, "Betaalrekening", "Supermarkt"],
      [26, 3_210, "Betaalrekening", "Supermarkt"],
      [27, 2_460, "Betaalrekening", "Supermarkt"],
      [28, 1_275, "Betaalrekening", "Koffie"],
    ];
    for (const [dayOfMonth, minor, account, category] of bills) {
      entries.push(expense(day(month, dayOfMonth), account, minor, category));
    }

    /* A card charge lands mid-month and the bill is cleared a few days later. The
       charge is spending; clearing it is not, and it RAISES net worth, because the
       liability that came back in is the asset that went out. A ledger that shows
       that as a loss is not modelling a card, it is modelling a bug. */
    entries.push(expense(day(month, 14), "Amex", 8_750 - index * 700, "Restaurant"));
    entries.push(expense(day(month, 21), "Amex", 4_290 + index * 613, "Kleding"));
    entries.push(transfer(day(month, 22), "Betaalrekening", "Amex", 96_400 - index * 4_200));
    entries.push(repayment(day(month, 23), 187_50));

    /* The savings transfer is the reason the balance grows at all. It moves money
       between two accounts the user owns, so it is not spending and not income —
       but it is still money arriving in Spaarrekening, and a series that ignores
       it would show someone getting steadily poorer while saving hard. */
    entries.push(transfer(day(month, 26), "Betaalrekening", "Spaarrekening", 75_000));

    /* One category with ONE transaction in the whole window. */
    if (back === -SINGLETON_MONTH) {
      entries.push(expense(day(month, 6), "Betaalrekening", 1_237, "Apotheek"));
    }

    /* A holiday, in the month that also carries the flat stretch. */
    if (back === -FLAT_STRETCH_MONTH) {
      entries.push(expense(day(month, 3), "Amex", 14_200, "Vakantie"));
    }
  });

  /* THE QUIET MONTH. Nothing at all posts from the 10th to the 28th: nineteen
   * days away, with the statements not imported until afterwards. A real ledger
   * has these. A gap in the raw data is not a smooth interpolation — it is
   * nineteen days where nobody knows what happened, and a series that drew a
   * slope straight through it would be inventing transactions.
   *
   * It is wide enough to swallow the salary and the rent, so the quiet month
   * genuinely reads as a month in which money was arriving and going out and none
   * of it had been filed yet. That is what the gap in the raw data means, and it
   * is the shape that breaks a chart which assumes every day has something in it.
   */
  return entries.filter((entry) => !insideFlatStretch(entry.date, months));
};

/** The day before the first of `month`. */
const monthBefore = (month: string): string => {
  const [year = 1970, monthNumber = 1] = month.split("-").map(Number);
  const date = new Date(year, monthNumber - 2, 0);
  return `${date.getFullYear()}-${`${date.getMonth() + 1}`.padStart(2, "0")}-${date.getDate()}`;
};

/** True when `date` falls inside the deliberate silence. Expressed as a test on
 * the date rather than as a filter over a list of exceptions, so adding an entry
 * to that month in the table above cannot accidentally land inside the stretch —
 * which is exactly what happened when the salary moved to the 25th and this
 * window was still fourteen to twenty-three. */
const insideFlatStretch = (date: string, months: readonly string[]): boolean => {
  const month = date.slice(0, 7);
  if (month !== months[MONTHS_OF_HISTORY - 1 - FLAT_STRETCH_MONTH]) {
    return false;
  }
  const dayOfMonth = Number(date.slice(8, 10));
  return dayOfMonth >= 10 && dayOfMonth <= 28;
};

/* ── Derivation ────────────────────────────────────────────────────────────
 * One function per upstream rule, each naming the rule it mirrors. These are not
 * a second implementation of the analytics: they are the demo's way of arriving at
 * the same numbers without a database. */

/** `analytics.net_worth`: the running sum of every asset and liability line up to
 * and including each date, emitted for EVERY day in the range — a series that
 * breaks on a quiet week reads as missing data rather than as a flat one.
 *
 * Equity is excluded: spending is parked in a seeded equity contra-account, and
 * counting it would make every purchase RAISE the figure it should lower.
 * Liabilities are added, not subtracted, because they are already negative. */
const deriveNetWorth = (entries: readonly Entry[], from: string, to: string): NetWorthPoint[] => {
  const sorted = [...entries].sort((a, b) => a.date.localeCompare(b.date));
  const dates: string[] = [];
  for (let cursor = from; cursor <= to; cursor = nextDay(cursor)) {
    dates.push(cursor);
  }

  let running = 0;
  let index = 0;
  return dates.map((date) => {
    while (index < sorted.length && (sorted[index]?.date ?? "") <= date) {
      for (const leg of sorted[index]?.legs ?? []) {
        const nature = NATURE.get(leg.account);
        if (nature === "asset" || nature === "liability") {
          running += leg.minor;
        }
      }
      index += 1;
    }
    return { date, net_worth: running };
  });
};

/** `analytics.spend_by_category`: income and expense on non-equity accounts with
 * a category, transfers excluded — moving money to savings is not spending.
 *
 * Grouped by the category on the entry, so a category that only ever had one
 * transaction produces one row of its own and nothing merges it away. */
const deriveSpendByCategory = (
  entries: readonly Entry[],
  from: string,
  to: string,
): SpendByCategoryPoint[] => {
  const totals = new Map<string, number>();

  for (const entry of entries) {
    if (entry.isTransfer || entry.category === undefined) {
      continue;
    }
    if (entry.date < from || entry.date > to) {
      continue;
    }
    const real = entry.legs.filter((leg) => NATURE.get(leg.account) !== "equity");
    const total = real.reduce((sum, leg) => sum + leg.minor, 0);
    const name = entry.category;
    const kind = total >= 0 ? "income" : "expense";
    const key = `${kind} ${name}`;
    const signed = totals.get(key) ?? 0;
    totals.set(key, signed + total);
  }

  const points: SpendByCategoryPoint[] = [];
  let nextId = 1;
  for (const [key, amount] of totals) {
    const [kind = "", name = ""] = key.split(" ");
    points.push({
      category_id: nextId,
      category_name: name,
      kind: kind === "income" ? "income" : "expense",
      amount,
    });
    nextId += 1;
  }

  // Largest spend first, exactly as upstream sorts them, so the chart's row order
  // does not depend on a database's row order.
  return points.sort((a, b) => a.amount - b.amount);
};

/** `analytics.cashflow`: asset accounts only — the equity contra-leg carries the
 * opposite sign to every real movement, so counting both sides would net each
 * period to zero — and any entry whose every leg is an asset is dropped entirely,
 * because that is money rearranging inside the user's own accounts.
 *
 * An entry that reaches a NON-asset account survives, which is what makes paying
 * a credit card read as cash leaving: the card is a liability, so the paying leg
 * is the only asset leg and the amount is not cancelled.
 *
 * A period with no movement is OMITTED rather than emitted as zero, so the chart
 * gets a real gap to leave empty. */
const deriveCashflow = (entries: readonly Entry[], from: string, to: string): CashflowBucket[] => {
  const buckets = new Map<string, { income: number; expense: number }>();

  for (const entry of entries) {
    if (entry.date < from || entry.date > to) {
      continue;
    }
    const everyLegIsAnAsset = entry.legs.every((leg) => NATURE.get(leg.account) === "asset");
    if (everyLegIsAnAsset) {
      continue;
    }
    const movement = entry.legs
      .filter((leg) => NATURE.get(leg.account) === "asset")
      .reduce((sum, leg) => sum + leg.minor, 0);
    if (movement === 0) {
      continue;
    }
    const key = entry.date.slice(0, 7);
    const bucket = buckets.get(key) ?? { income: 0, expense: 0 };
    if (movement > 0) {
      bucket.income += movement;
    } else {
      bucket.expense += -movement;
    }
    buckets.set(key, bucket);
  }

  return [...buckets.entries()]
    .map(([period, bucket]) => ({
      period,
      income: bucket.income,
      expense: bucket.expense,
      net: bucket.income - bucket.expense,
    }))
    .sort((a, b) => a.period.localeCompare(b.period));
};

/** The demo's whole story, derived for the window ending today. */
export const buildDemoOverview = (
  today: Date = new Date(),
): {
  netWorth: NetWorthPoint[];
  spendByCategory: SpendByCategoryPoint[];
  cashflow: CashflowBucket[];
  /** Every month in the window, INCLUDING the one cashflow deliberately omits.
   * The chart needs the full axis to draw a gap against, or the gap lands
   * wherever an adjacent month happens to sit. */
  months: string[];
} => {
  const months: string[] = [];
  for (let back = MONTHS_OF_HISTORY - 1; back >= 0; back -= 1) {
    months.push(monthKey(today, -back));
  }

  const from = `${months[0] ?? monthKey(today, 0)}-01`;
  const to = `${monthKey(today, 0)}-${`${today.getDate()}`.padStart(2, "0")}`;

  const entries = ledger(months);
  return {
    netWorth: deriveNetWorth(entries, from, to),
    spendByCategory: deriveSpendByCategory(entries, from, to),
    cashflow: deriveCashflow(entries, from, to),
    months,
  };
};

/** What the demo puts in the review queue.
 *
 * Deliberately non-empty, so the stub shows its lime state; the cyan state is
 * reached by turning demo off, which is the honest way to see both. */
export const DEMO_REVIEW = { open: 7 } as const;

/** When the next statement is due. Imports are monthly, so the next one is the
 * first of next month: a real cadence with a real answer, rather than a
 * countdown to nothing. */
export const demoNextImport = (
  today: Date = new Date(),
): { dueDate: string; inDays: number } => {
  const due = new Date(today.getFullYear(), today.getMonth() + 1, 1);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return {
    dueDate: `${due.getFullYear()}-${`${due.getMonth() + 1}`.padStart(2, "0")}-01`,
    inDays: Math.round((due.getTime() - startOfToday.getTime()) / 86_400_000),
  };
};

/** Named here so a test can assert the awkward cases exist rather than trusting
 * that they do. */
export const DEMO_SINGLETON_CATEGORY = "Apotheek";

/** The month offsets the awkward cases land on, in the same units `ledger` uses,
 * so a test can point at the right month without re-deriving the arithmetic. */
export const DEMO_CASES = {
  noSpendingMonthOffset: NO_SPENDING_MONTH,
  flatStretchMonthOffset: FLAT_STRETCH_MONTH,
  singletonCategoryMonthOffset: SINGLETON_MONTH,
  singletonCategory: DEMO_SINGLETON_CATEGORY,
} as const;

/** The account rows the demo opens, in the shape `use-accounts` returns, so the
 * account count on the amount box comes from the same list rather than being
 * stated separately and drifting. */
export const DEMO_ACCOUNT_SUMMARY = ACCOUNTS.map((account, index) => ({
  id: index + 1,
  name: account.name,
  currency: "EUR",
  account_type: account.account_type,
  account_nature: account.account_nature,
  is_active: true,
  is_hidden: false,
  sort_order: index,
}));

/** The account count that produced the figure: assets and liabilities only.
 * The equity account is what makes expenses balance, and counting it would
 * inflate the very number it is offsetting. */
export const DEMO_COUNTED_ACCOUNTS = ACCOUNTS.filter(
  (account) => account.account_nature !== "equity",
).length;