/* Imports hook — stub */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { ImportBatch } from "./types";

export const useImports = () => {
  const [data, setData] = React.useState<ImportBatch[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<ImportBatch[]>("/finance/imports")
      .then(setData)
      .catch(() => setData([]))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};

export type ImportsState = ReturnType<typeof useImports>;

/* The provider owns the single fetch; pages read through this context. */
export const ImportsContext = React.createContext<ImportsState | null>(null);

export const useImportsContext = () => {
  const state = React.useContext(ImportsContext);
  if (state === null) {
    throw new Error("useImportsContext must be used within an <ImportsProvider>");
  }
  return state;
};
