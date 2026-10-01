/* Imports hook — stub */
import React from "react";
import { apiGet } from "@/lib/apiClient";
import type { ImportBatch } from "./imports";

export const useImports = () => {
  const [data, setData] = React.useState<ImportBatch[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<ImportBatch[]>("/finance/imports")
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return { data, loading };
};