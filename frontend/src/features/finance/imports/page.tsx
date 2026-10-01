/* Imports page — stub component. */
import React from "react";
import { useImports } from "./use-imports";

export const ImportsPage: React.FC = () => {
  const [_importBatches, loading] = useImports();

  if (loading) {
    return <p>Loading imports…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Imports</h2>
      <p className="text-muted-foreground">No import batches.</p>
    </section>
  );
};