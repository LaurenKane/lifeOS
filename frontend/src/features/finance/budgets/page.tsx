/* Budgets page — planned against actual, per category.
 *
 * The read comes from BudgetsProvider. Actual comes from the finance module's
 * own `/analytics/spend-by-category` endpoint, fetched HERE (app-layer
 * composition — the budgets screen reads what the overview card reads and
 * nothing else), windowed to each budget's period RUNNING UP TO TODAY: a
 * monthly budget is not judged against a full month when the month is half
 * done.
 *
 * Rows render only what the ledger has evidence for; a category with no
 * spending shows actual "—" rather than a confident zero. Over the planned
 * amount, the bar reads in the money-out tone — again information, never
 * shame; the number and its truth are the whole message.
 */
import React from "react";
import { z } from "zod";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { API_PATH, apiGet, describeError, expectSchema } from "@/lib/apiClient";
import { formatMinorUnits } from "@/lib/money";
import { useBudgetsContext } from "./use-budgets";
import type { Budget } from "./types";

/** The spend points, mirrored (no cross-feature import): same shape as the
 * overview's — signed minor units, expenses NEGATIVE. */
const SpendByCategoryPoint = z.object({
  category_id: z.number().int(),
  category_name: z.string(),
  kind: z.enum(["expense", "income", "transfer", "investment"]),
  amount: z.number().int(),
});

const windowStart = (period: Budget["period"], now: Date): Date => {
  const start = new Date(now);
  if (period === "monthly") {
    start.setDate(1);
  } else if (period === "quarterly") {
    start.setDate(1);
    start.setMonth(Math.floor(start.getMonth() / 3) * 3);
  } else {
    start.setMonth(0, 1);
  }
  start.setHours(0, 0, 0, 0);
  return start;
};

const isoOf = (d: Date): string => d.toISOString().slice(0, 10);

const BudgetRow: React.FC<{ budget: Budget; actual: number | null }> = ({
  budget,
  actual,
}) => {
  const planned = budget.amountMinor;
  const useSign = actual !== null;
  /* A spend is NEGATIVE in the ledger; "£21.33 spent" is the reading. */
  const spent = useSign ? -actual : null;
  const over = spent !== null && spent > planned;
  const ratio = spent === null || planned === 0 ? 0 : Math.min(spent / planned, 1);
  return (
    <li className="px-6 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <span className="text-[0.9375rem]">{budget.name}</span>
        <span className="eyebrow text-ink-quiet">{budget.period}</span>
        <span className="font-mono text-[0.875rem] tabular-nums text-ink-quiet">
          {spent === null
            ? "no spending yet"
            : `${formatMinorUnits(spent, budget.currency)} of ${formatMinorUnits(planned, budget.currency)}`}
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded bg-ground">
        {ratio > 0 && (
          <div
            className={over ? "h-full bg-money-out" : "h-full bg-ink"}
            style={{ width: `${ratio * 100}%` }}
          />
        )}
      </div>
    </li>
  );
};

export const BudgetsPage: React.FC = () => {
  const { data, loading, error } = useBudgetsContext();

  const [actuals, setActuals] = React.useState<Map<number, number> | null>(null);
  const [actualError, setActualError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (data === null || data.length === 0) return;
    let alive = true;
    const now = new Date();
    /* One request for the widest needed window; rows window their own bar by period. */
    const widest = data.some((b) => b.period === "yearly")
      ? "yearly"
      : data.some((b) => b.period === "quarterly")
        ? "quarterly"
        : "monthly";
    apiGet<unknown>(
      `${API_PATH}/analytics/spend-by-category?from=${isoOf(windowStart(widest, now))}&to=${isoOf(now)}&rollup=true`,
    )
      .then((raw) => {
        const points = expectSchema(SpendByCategoryPoint.array(), raw, "spend-by-category");
        if (!alive) return;
        const perCategory = new Map<number, number>();
        for (const point of points) {
          const existing = perCategory.get(point.category_id) ?? 0;
          perCategory.set(point.category_id, existing + point.amount);
        }
        setActuals(perCategory);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        /* An actual read that fails does not erase the plan — the planned
         * column still renders, and the failure is said rather than swallowed. */
        setActualError(describeError(cause));
      });
    return () => {
      alive = false;
    };
  }, [data]);

  return (
    <AppShell>
      <PageHeader
        title="Budgets"
        description="Planned against actual, per category, over the period each budget names."
      />
      <Panel>
        {loading ? (
          <Skeleton label="loading budgets" rows={3} />
        ) : error !== null ? (
          <div className="p-5">
            <Notice tone="warning" label="Couldn't load budgets">
              {error}
            </Notice>
          </div>
        ) : data === null || data.length === 0 ? (
          <EmptyState title="No budgets defined." />
        ) : (
          <ul className="flex flex-col">
            {data.map((budget) => (
              <BudgetRow
                key={budget.id}
                budget={budget}
                actual={actuals?.get(budget.category_id) ?? null}
              />
            ))}
          </ul>
        )}
        {actualError !== null && (
          <div className="p-5">
            <Notice tone="info" label="Couldn't load the actual figures">
              {actualError}
            </Notice>
          </div>
        )}
      </Panel>
    </AppShell>
  );
};
