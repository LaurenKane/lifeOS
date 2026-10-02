/* Review hook — stub */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { ReviewItem } from "./types";

export const useReview = () => {
  const [data, setData] = React.useState<ReviewItem[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<ReviewItem[]>("/finance/review")
      .then(setData)
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
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
