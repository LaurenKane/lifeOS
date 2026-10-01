/* Budgets page — stub component. */
import React from "react";
import { useBudgetsContext } from "./use-budgets";

export const BudgetsPage: React.FC = () => {
  const { data, loading } = useBudgetsContext();

  if (loading) {
    return <p>Loading budgets…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Budgets</h2>
      <p className="text-muted-foreground">
        {data.length === 0 ? "No budgets defined." : `${data.length} budgets.`}
      </p>
    </section>
  );
};
