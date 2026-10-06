/* Review provider — owns the queue fetch, the counts and the three decisions,
 * and exposes them to pages through ReviewContext.
 *
 * It renders its children immediately. The previous shape held the whole app
 * behind its own fetch; because these providers nest around the router, one
 * slow collection used to decide what every route displayed. Each page owns
 * its own loading and error states now. */
import React from "react";
import { ReviewContext, useReview } from "./use-review";

export const ReviewProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <ReviewContext.Provider value={useReview()}>{children}</ReviewContext.Provider>;
