/* Review page — stub. The endpoint returns an empty list today (an M0 stub
 * with no tables behind it), so this page's job is to say that plainly rather
 * than to imply the queue is being kept up. */
import React from "react";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { useReviewContext } from "./use-review";

export const ReviewPage: React.FC = () => {
  const { data, loading, error } = useReviewContext();

  return (
    <AppShell>
      <PageHeader
        title="Review queue"
        description="Transactions the pipeline could not decide on by itself."
      />
      <Panel>
        {error !== null ? (
          <div className="p-5">
            <Notice tone="error" label="The queue could not be read">
              {error}
            </Notice>
          </div>
        ) : loading ? (
          <Skeleton label="Loading review queue" rows={3} />
        ) : data.length === 0 ? (
          <EmptyState title="No items in review queue.">
            Nothing has been queued. The decisions behind this queue — confirm,
            reject, ignore — arrive with import handling, which is a later
            milestone.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-border">
            {data.map((item) => (
              <li key={item.id} className="px-5 py-3.5 text-sm">
                {item.description}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </AppShell>
  );
};
