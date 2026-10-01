/* Review hook — stub */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { ReviewItem } from "./review";

export const useReview = () => {
  const [data, setData] = React.useState<ReviewItem[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<ReviewItem[]>("/finance/review")
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};