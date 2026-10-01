/* Budgets page — stub component. */
import React from "react";
import { useBudgets } from "./use-budgets";

export const BudgetsPage: React.FC = () => {
  const [_budgets, loading] = useBudgets();

  if (loading) {
    return <p>Loading budgets…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Budgets</h2>
      <p className="text-muted-foreground">No budgets defined.</p>
    </section>
  );
};