/* Imports page — stub component. */
import React from "react";
import { useImportsContext } from "./use-imports";

export const ImportsPage: React.FC = () => {
  const { data, loading } = useImportsContext();

  if (loading) {
    return <p>Loading imports…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Imports</h2>
      <p className="text-muted-foreground">
        {data.length === 0 ? "No import batches." : `${data.length} import batches.`}
      </p>
    </section>
  );
};
