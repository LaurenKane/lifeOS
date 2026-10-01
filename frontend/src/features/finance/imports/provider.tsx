/* Imports provider — stub. Owns the single imports fetch and exposes the
 * result to pages via ImportsContext.
 */
import React from "react";
import { ImportsContext, useImports } from "./use-imports";

export const ImportsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const state = useImports();

  if (state.loading) {
    return <p>Loading imports…</p>;
  }

  return (
    <ImportsContext.Provider value={state}>
      <div>{children}</div>
    </ImportsContext.Provider>
  );
};
