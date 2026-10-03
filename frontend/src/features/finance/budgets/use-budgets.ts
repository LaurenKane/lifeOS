/* Budgets hook — stub.
 *
 * There is no budgets endpoint on the backend at all — budgets is a concept
 * from a later milestone. This hook therefore always ends in `error`, and the
 * page says so. The old `.catch(() => setData([]))` made an absent endpoint
 * look like an empty one, which is how a missing feature hides for months.
 *
 * The path keeps the same shape as the rest of the app so that when the
 * endpoint does appear, only the base changes. */
import React from "react";
import { API_PATH, apiGet, describeError } from "@/lib/apiClient";
import type { Budget } from "./types";

export const useBudgets = () => {
  const [data, setData] = React.useState<Budget[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<Budget[]>(`${API_PATH}/budgets`)
      .then(setData)
      .catch((cause: unknown) => setError(describeError(cause)))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading, error };
};

export type BudgetsState = ReturnType<typeof useBudgets>;

/* The provider owns the single fetch. Pages read through this context instead
 * of calling useBudgets() again, which would issue a second request for the
 * same data. */
export const BudgetsContext = React.createContext<BudgetsState | null>(null);

export const useBudgetsContext = () => {
  const state = React.useContext(BudgetsContext);
  if (state === null) {
    throw new Error("useBudgetsContext must be used within a <BudgetsProvider>");
  }
  return state;
};
