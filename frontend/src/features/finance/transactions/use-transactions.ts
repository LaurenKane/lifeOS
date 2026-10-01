/* Transactions hook — stub. Real implementation will fetch from
 * the API client in src/lib/apiClient.ts and manage state with a
 * lightweight store or React Query / TanStack Query.
 */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { Transaction } from "./types";

export const useTransactions = () => {
  const [data, setData] = React.useState<Transaction[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<Transaction[]>("/finance/transactions")
      .then(setData)
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};

export type TransactionsState = ReturnType<typeof useTransactions>;

/* The provider owns the single fetch; pages read through this context. */
export const TransactionsContext = React.createContext<TransactionsState | null>(null);

export const useTransactionsContext = () => {
  const state = React.useContext(TransactionsContext);
  if (state === null) {
    throw new Error("useTransactionsContext must be used within a <TransactionsProvider>");
  }
  return state;
};
