/* Review page — stub component. */
import React from "react";
import { useReview } from "./use-review";

export const ReviewPage: React.FC = () => {
  const [_reviewItems, loading] = useReview();

  if (loading) {
    return <p>Loading review queue…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Review Queue</h2>
      <p className="text-muted-foreground">No items in review queue.</p>
    </section>
  );
};