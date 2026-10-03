/* Review provider — stub. Owns the single review fetch and exposes it to pages
 * via ReviewContext. It renders its children immediately; pages own their own
 * loading and error states. */
import React from "react";
import { ReviewContext, useReview } from "./use-review";

export const ReviewProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <ReviewContext.Provider value={useReview()}>{children}</ReviewContext.Provider>;
