/* Categories provider — owns the category read, the kind read, the rule read
 * and the three writes, and exposes them to pages through CategoriesContext.
 *
 * It renders its children immediately, like every other provider here: these
 * nest around the router, and one held-back fetch would decide what every route
 * displays. Each page owns its own loading and error states. */
import React from "react";
import { CategoriesContext, useCategories } from "./use-categories";

export const CategoriesProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <CategoriesContext.Provider value={useCategories()}>{children}</CategoriesContext.Provider>;