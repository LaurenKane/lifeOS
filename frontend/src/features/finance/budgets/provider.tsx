/* Budgets provider — stub. Owns the single budgets fetch and exposes
 * the result to pages via BudgetsContext.
 */
import React from "react";
import { BudgetsContext, useBudgets } from "./use-budgets";

export const BudgetsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const state = useBudgets();

  if (state.loading) {
    return <p>Loading budgets…</p>;
  }

  return (
    <BudgetsContext.Provider value={state}>
      <div>{children}</div>
    </BudgetsContext.Provider>
  );
};
