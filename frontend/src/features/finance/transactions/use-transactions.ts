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
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};