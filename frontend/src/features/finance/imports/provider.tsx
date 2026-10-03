/* Imports provider — stub. Owns the single imports fetch and exposes it to
 * pages via ImportsContext. It renders its children immediately. */
import React from "react";
import { ImportsContext, useImports } from "./use-imports";

export const ImportsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <ImportsContext.Provider value={useImports()}>{children}</ImportsContext.Provider>;
