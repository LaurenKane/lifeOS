/* Budgets provider — stub */
import React from "react";
import { useBudgets } from "./use-budgets";

export const BudgetsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { data: budgets, loading } = useBudgets();

  if (loading) {
    return <p>Loading budgets…</p>;
  }

  return <div>{children}</div>;
};