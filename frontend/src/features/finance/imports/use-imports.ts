/* Imports hook — stub. Same two corrections as the review hook: a failed fetch
 * is kept rather than turned into "no import batches", and the path is
 * `/v1/imports` because every router hangs off `/api/v1`. */
import React from "react";
import { API_PATH, apiGet, describeError } from "@/lib/apiClient";
import type { ImportBatch } from "./types";

export const useImports = () => {
  const [data, setData] = React.useState<ImportBatch[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<ImportBatch[]>(`${API_PATH}/imports`)
      .then(setData)
      .catch((cause: unknown) => setError(describeError(cause)))
      .finally(() => setLoading(false));
  }, []);

  return { data, loading, error };
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
