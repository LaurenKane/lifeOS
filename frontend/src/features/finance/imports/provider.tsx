/* Imports provider — stub */
import React from "react";
import { useImports } from "./use-imports";

export const ImportsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { data: importBatches, loading } = useImports();

  if (loading) {
    return <p>Loading imports…</p>;
  }

  return <div>{children}</div>;
};