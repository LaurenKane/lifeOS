/* Review page — stub component. */
import React from "react";
import { useReviewContext } from "./use-review";

export const ReviewPage: React.FC = () => {
  const { data, loading } = useReviewContext();

  if (loading) {
    return <p>Loading review queue…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Review Queue</h2>
      <p className="text-muted-foreground">
        {data.length === 0
          ? "No items in review queue."
          : `${data.length} items in review queue.`}
      </p>
    </section>
  );
};
