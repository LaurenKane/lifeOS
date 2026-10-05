/* ── Overview hook ─────────────────────────────────────────────────────────
 * Three reads, one decision, and the decision is the point of this file.
 *
 * THE THREE STATES, AND WHY THEY MUST NOT LOOK ALIKE
 * -------------------------------------------------
 *   real        the API answered. What it said is what is on screen.
 *   real-empty  the API answered and the ledger is empty. This is a REAL state
 *               and it renders as one: a panel that names what is missing and no
 *               figure at all, rather than a zero.
 *   demo        the API was not asked. Figures are invented, and the page says so
 *               in three separate places while they are on screen.
 *
 * There is no fourth state, and specifically there is no path on which a failed
 * or empty read falls back to the demo. A finance tool that renders a plausible
 * invented number when the database is empty is the worst thing this product
 * could do, so the demo is unreachable except by an explicit act — clicking the
 * control, or arriving with `?demo=1`.
 *
 * The `?demo=1` route exists because the control has to be reachable in a
 * screenshot review and in a shared link without anyone having to click through a
 * first-run state to get there. It is one-way: turning the demo off from the UI
 * clears the parameter, so the URL and the screen cannot disagree about which of
 * the two a reload will show.
 *
 * WHY NOTHING HERE IS STATE THAT COULD BE DERIVED
 * -----------------------------------------------
 * The demo figures are `useMemo`, not an effect that calls `setState`: they are
 * local arithmetic over a local list, so deriving them during render is both
 * correct and one render cheaper. Only the network result is state, because only
 * the network result is not knowable at render time.
 */
import React from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { API_PATH, apiGet, describeError, expectSchema } from "@/lib/apiClient";
import { useAccountsContext } from "@/features/finance/accounts/use-accounts";
import { useTransactionsContext } from "@/features/finance/transactions/use-transactions";
import type { AccountSummary } from "@/features/finance/accounts/types";
import { CashflowSchema, NetWorthSeriesSchema, SpendByCategorySchema } from "./types";
import type { CashflowBucket, NetWorthPoint, SpendByCategoryPoint } from "./types";
import {
  DEMO_ACCOUNT_SUMMARY,
  DEMO_COUNTED_ACCOUNTS,
  DEMO_REVIEW,
  buildDemoOverview,
  demoNextImport,
} from "./demo";

/** How much history the page asks for.
 *
 * Six months, for the same reason the demo carries six: a chart needs enough width
 * to show a flat stretch or a missing month at all. The net-worth series is daily
 * across that window, so it is the one figure on the page whose delta is a real
 * comparison rather than a remembered one. */
const HISTORY_MONTHS = 6;

const monthStart = (date: Date, back: number): string => {
  const index = date.getMonth() - back;
  const year = date.getFullYear() + Math.floor(index / 12);
  const month = ((index % 12) + 12) % 12;
  return `${year}-${`${month + 1}`.padStart(2, "0")}`;
};

/** Everything the page renders, already resolved and already honest about where it
 * came from. Nothing downstream re-asks. */
export type OverviewData = {
  netWorth: NetWorthPoint[];
  spendByCategory: SpendByCategoryPoint[];
  cashflow: CashflowBucket[];
  /** Assets and liabilities only. The equity account is what makes expenses
   * balance and counting it would inflate the number it offsets. */
  accounts: AccountSummary[];
  accountCount: number;
  /** How many transactions are waiting on a category, or `null` when that has
   * NOT been established. The distinction is the whole point of the review stub:
   * a failed read is not a clear queue, and a panel that reports zero because a
   * request failed is telling a reader their money is tidy when nobody has
   * looked. */
  reviewOpen: number | null;
  nextImport: { dueDate: string; inDays: number } | null;
  /** Whether the ledger is genuinely empty. Decided by the ACCOUNTS, not by the
   * figures: an empty series with accounts open is a ledger with postings
   * missing, which is a different problem from one that has never been used. */
  empty: boolean;
};

export type OverviewState = OverviewData & {
  demo: boolean;
  loading: boolean;
  /** The server's own words, or nothing. Never swallowed into an empty state. */
  error: string | null;
  reload: () => void;
};

/** The one honest reading of "there is nothing here".
 *
 * A zero is a number, and printing `0.00` on an account that was never opened is a
 * claim — it says the user is worth nothing rather than that nothing has been
 * recorded. So emptiness is decided from the account list, and the figure is
 * withheld entirely when it is true. */
const isEmpty = (accounts: AccountSummary[]): boolean =>
  accounts.filter((account) => account.account_nature !== "equity").length === 0;

const emptyState = (): OverviewData => ({
  netWorth: [],
  spendByCategory: [],
  cashflow: [],
  accounts: [],
  accountCount: 0,
  reviewOpen: null,
  nextImport: null,
  empty: true,
});

/**
 * Whether the current URL asks for the demo.
 *
 * Read from the ROUTER's search params rather than `window.location`, and that is
 * not a stylistic preference: `window.location` is the browser's history, which
 * is not the only thing a router in this app can be reading from. A test mounts
 * the same routes on a memory router, and there the two disagree — the parameter
 * would be invisible and the page would quietly render real figures for a link
 * that said otherwise, which for this page is the one failure that matters most.
 */
const demoRequested = (search: string): boolean =>
  new URLSearchParams(search).get("demo") === "1";

/** What one real read settled with. `null` while in flight.
 *
 * Keyed by the attempt rather than by a boolean, the same shape
 * `use-transactions.ts` uses for a single record: a refetch re-arms the state, so
 * the page cannot show a stale success while a new request is in the air, and the
 * loading flag is DERIVED from it instead of set imperatively. */
type Settled = {
  attempt: number;
  data: OverviewData;
  error: string | null;
} | null;

export const useOverview = (): OverviewState & { setDemo: (on: boolean) => void } => {
  const accountsContext = useAccountsContext();
  const transactionsContext = useTransactionsContext();
  const location = useLocation();
  const [, setSearchParams] = useSearchParams();

  /* Once per mount, in an initialiser rather than during render: `new Date()` is
   * impure, and reading the clock on every render would make the "six months
   * ending today" window a value that changes under the reader. */
  const [today] = React.useState(() => new Date());

  /* The mode FOLLOWS the URL rather than living beside it. Two sources of truth
   * for one fact is how a screen ends up disagreeing with its own address bar, and
   * this page's whole argument is that what you see and what is true are the same
   * thing. */
  const demo = demoRequested(location.search);

  const [attempt, setAttempt] = React.useState(0);
  const [settled, setSettled] = React.useState<Settled>(null);

  /** The queue this stub counts: money out with no category.
   *
   * Derived from the transactions list rather than fetched again. A review item
   * with no category IS an item awaiting a decision — that is what the review
   * page's own queue is — and a second request for the same answer would be a
   * second number that could disagree with the first.
   *
   * `null` while the list is in flight or after it failed, because "not looked
   * at" and "looked at and found nothing" are different answers and only one of
   * them is a clear queue. */
  const reviewOpen = React.useMemo(() => {
    if (transactionsContext.loading || transactionsContext.error !== null) {
      return null;
    }
    return transactionsContext.data.filter(
      (row) => row.category_id === null && row.raw_amount < 0,
    ).length;
  }, [transactionsContext.data, transactionsContext.loading, transactionsContext.error]);

  /* The demo, derived. Nothing to wait for: these are local arithmetic over a
   * local list, and making them appear a beat late would suggest they came from
   * somewhere. */
  const demoData = React.useMemo<OverviewData>(() => {
    const built = buildDemoOverview(today);
    return {
      netWorth: built.netWorth,
      spendByCategory: built.spendByCategory,
      cashflow: built.cashflow,
      accounts: [...DEMO_ACCOUNT_SUMMARY],
      accountCount: DEMO_COUNTED_ACCOUNTS,
      reviewOpen: DEMO_REVIEW.open,
      nextImport: demoNextImport(today),
      empty: false,
    };
  }, [today]);

  /* Real figures. Guarded on `demo` so that turning the demo ON cannot leave a
   * real response in flight about to overwrite it — the race would be brief and it
   * would show the wrong numbers on a screen that claims they are invented. */
  React.useEffect(() => {
    if (demo) {
      return;
    }

    let current = true;
    const from = monthStart(today, HISTORY_MONTHS - 1);
    const range = `from=${from}-01`;

    Promise.all([
      apiGet<unknown>(`${API_PATH}/analytics/net-worth?${range}`),
      apiGet<unknown>(`${API_PATH}/analytics/spend-by-category?${range}&rollup=true`),
      apiGet<unknown>(`${API_PATH}/analytics/cashflow?${range}&period=month`),
    ])
      .then(([netWorth, spend, cashflow]) => {
        if (!current) {
          return;
        }
        /* Accounts arrive from the provider every other page already uses, so the
         * count on this page is the same count the accounts page would show and
         * not a second, separately-maintained number. */
        const accounts = accountsContext.accounts;
        const counted = accounts.filter((account) => account.account_nature !== "equity");

        setSettled({
          attempt,
          error: null,
          data: {
            netWorth: expectSchema(NetWorthSeriesSchema, netWorth, "net-worth series"),
            spendByCategory: expectSchema(SpendByCategorySchema, spend, "spend-by-category"),
            cashflow: expectSchema(CashflowSchema, cashflow, "cashflow"),
            accounts,
            accountCount: counted.length,
            reviewOpen,
            /* No endpoint states when the next import is due — imports are a file
             * the user brings, not a schedule the app keeps. Null means the panel
             * says so instead of inventing a date. */
            nextImport: null,
            empty: isEmpty(accounts),
          },
        });
      })
      .catch((cause: unknown) => {
        if (current) {
          setSettled({ attempt, data: emptyState(), error: describeError(cause) });
        }
      });

    return () => {
      current = false;
    };
    /* The accounts and transactions providers settle on their own schedule, so
     * they are dependencies rather than something waited on: an account list that
     * arrives late re-derives the count and the empty state, and re-reading the
     * aggregates is a cheap request against a local database. */
  }, [demo, today, attempt, accountsContext.accounts, reviewOpen]);

  /* Writing through the router rather than `history.replaceState` directly, so the
   * navigation state and the address bar cannot drift apart — and with
   * `{replace: true}`, because this is a mode and not a place: a back button that
   * walks through mode changes is a nuisance rather than a navigation. */
  const setDemo = React.useCallback(
    (on: boolean) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (on) {
            next.set("demo", "1");
          } else {
            next.delete("demo");
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  /* The click re-arms the attempt; the effect makes the request. */
  const reload = React.useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  if (demo) {
    return { ...demoData, demo, loading: false, error: null, reload, setDemo };
  }

  const settledNow = settled?.attempt === attempt ? settled : null;
  return {
    ...(settledNow?.data ?? emptyState()),
    demo,
    loading: settledNow === null,
    error: settledNow?.error ?? null,
    reload,
    setDemo,
  };
};