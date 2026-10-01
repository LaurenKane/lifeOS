/* Budgets hook — stub */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { Budget } from "./budgets";

export const useBudgets = () => {
  const [data, setData] = React.useState<Budget[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<Budget[]>("/finance/budgets")
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};