/* Budgets hook — stub. Real implementation will fetch from
 * the API client in src/lib/apiClient.ts and manage state with a
 * lightweight store or React Query / TanStack Query.
 */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { Budget } from "./types";

export const useBudgets = () => {
  const [data, setData] = React.useState<Budget[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<Budget[]>("/finance/budgets")
      .then(setData)
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};

export type BudgetsState = ReturnType<typeof useBudgets>;

/* The provider owns the single fetch. Pages read through this context
 * instead of calling useBudgets() again, which would issue a second
 * request for the same data. */
export const BudgetsContext = React.createContext<BudgetsState | null>(null);

export const useBudgetsContext = () => {
  const state = React.useContext(BudgetsContext);
  if (state === null) {
    throw new Error("useBudgetsContext must be used within a <BudgetsProvider>");
  }
  return state;
};
