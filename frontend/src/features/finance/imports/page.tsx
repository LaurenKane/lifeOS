/* Imports page — stub. Import runs arrive with a later milestone; today there is
 * nothing here but the honest statement that there is nothing here. */
import React from "react";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { useImportsContext } from "./use-imports";

export const ImportsPage: React.FC = () => {
  const { data, loading, error } = useImportsContext();

  return (
    <AppShell>
      <PageHeader
        title="Imports"
        description="Bank and card statements, brought in as raw evidence."
      />
      <Panel>
        {error !== null ? (
          <div className="p-5">
            <Notice tone="error" label="Imports could not be read">
              {error}
            </Notice>
          </div>
        ) : loading ? (
          <Skeleton label="Loading imports" rows={3} />
        ) : data.length === 0 ? (
          <EmptyState title="No import batches.">
            Nothing has been imported. File imports arrive with the providers that
            read them, which is a later milestone — manual entry is the only way in
            for now.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border">
            {data.map((batch) => (
              <li key={batch.id} className="px-5 py-3.5 text-sm">
                {batch.sourceFilename}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </AppShell>
  );
};
