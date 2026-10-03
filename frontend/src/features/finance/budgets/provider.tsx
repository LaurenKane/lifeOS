/* Budgets provider — stub. Owns the single budgets fetch and exposes it to pages
 * via BudgetsContext. It renders its children immediately. */
import React from "react";
import { BudgetsContext, useBudgets } from "./use-budgets";

export const BudgetsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <BudgetsContext.Provider value={useBudgets()}>{children}</BudgetsContext.Provider>;
