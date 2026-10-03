/* Review hook — stub.
 *
 * Two changes from the stub shape, both because the old one lied:
 *
 *   - `.catch(() => setData([]))` turned a backend that is down into "nothing
 *     in the review queue". `error` is part of the state now.
 *   - the path was `/finance/review`, which is not mounted anywhere: every
 *     router hangs off `API_V1_PREFIX` = `/api/v1`, so the real read is
 *     `/v1/review` against the `/api` base.
 *
 * The endpoint itself is still an M0 stub returning an empty list, which is a
 * fact about the backend and not something the client should dress up. */
import React from "react";
import { API_PATH, apiGet, describeError } from "@/lib/apiClient";
import type { ReviewItem } from "./types";

export const useReview = () => {
  const [data, setData] = React.useState<ReviewItem[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<ReviewItem[]>(`${API_PATH}/review`)
      .then(setData)
      .catch((cause: unknown) => setError(describeError(cause)))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading, error };
};

export type ReviewState = ReturnType<typeof useReview>;

/* The provider owns the single fetch; pages read through this context. */
export const ReviewContext = React.createContext<ReviewState | null>(null);

export const useReviewContext = () => {
  const state = React.useContext(ReviewContext);
  if (state === null) {
    throw new Error("useReviewContext must be used within a <ReviewProvider>");
  }
  return state;
};
