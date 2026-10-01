/* Review provider — stub */
import React from "react";
import { useReview } from "./use-review";

export const ReviewProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { data: reviewItems, loading } = useReview();

  if (loading) {
    return <p>Loading review queue…</p>;
  }

  return <div>{children}</div>;
};