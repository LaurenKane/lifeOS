/* Merchants provider — owns the two reads and the five writes, and exposes them
 * to pages through MerchantsContext.
 *
 * It renders its children immediately, like every other provider here: these
 * nest around the router, and one held-back fetch would decide what every route
 * displays. Each page owns its own loading and error states.
 *
 * IT READS THE CATEGORY LIST FROM THE CATEGORIES PROVIDER, NOT ITSELF.
 * Every row on this screen is grouped by the kind of the category it points at,
 * which is the same answer the category picker gives, and a second read of
 * `/categories` would be two sources of truth for one dropdown — two lists that
 * could disagree after one of them reloaded. So `MerchantsProvider` must nest
 * INSIDE `CategoriesProvider` in `main.tsx`, the way `TransactionsProvider`
 * does; reading `useCategoriesContext` from a provider that wrapped it would
 * throw on first render. */
import React from "react";
import { MerchantsContext, useMerchants } from "./use-merchants";

export const MerchantsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <MerchantsContext.Provider value={useMerchants()}>{children}</MerchantsContext.Provider>;
