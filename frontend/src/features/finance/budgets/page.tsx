/* Budgets page — stub, and there is no endpoint behind it. The page says so in
 * the first line rather than rendering an empty list, because an empty list is
 * what a working feature looks like when it has no data yet. */
import React from "react";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/primitives";
import { useBudgetsContext } from "./use-budgets";

export const BudgetsPage: React.FC = () => {
  const { error } = useBudgetsContext();

  return (
    <AppShell>
      <PageHeader
        title="Budgets"
        description="Planned against actual, per category."
      />
      <Panel>
        {error !== null ? (
          <div className="p-5">
            <Notice tone="warning" label="Not built yet">
              {error}
            </Notice>
          </div>
        ) : (
          <EmptyState title="No budgets defined." />
        )}
      </Panel>
    </AppShell>
  );
};
