/* Accounts provider — owns the accounts fetch and exposes it to pages.
 *
 * It renders its children immediately. The previous shape returned
 * `<p>Loading…</p>` until its own fetch settled, and because these providers
 * nest around the whole router, the slowest one decided what every route
 * displayed — a page could not show its own loading or error state at all.
 * Each page owns its states now. */
import React from "react";
import { AccountsContext, useAccounts } from "./use-accounts";

export const AccountsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <AccountsContext.Provider value={useAccounts()}>{children}</AccountsContext.Provider>;
