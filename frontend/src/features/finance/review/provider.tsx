/* Review provider — stub. Owns the single review fetch and exposes the
 * result to pages via ReviewContext.
 */
import React from "react";
import { ReviewContext, useReview } from "./use-review";

export const ReviewProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const state = useReview();

  if (state.loading) {
    return <p>Loading review queue…</p>;
  }

  return (
    <ReviewContext.Provider value={state}>
      <div>{children}</div>
    </ReviewContext.Provider>
  );
};
