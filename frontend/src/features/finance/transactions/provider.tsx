/* Transactions provider — owns the transactions fetch and every write.
 *
 * It renders its children immediately. The previous shape returned
 * `<p>Loading transactions…</p>` until its own fetch settled, and because these
 * providers nest around the whole router, one slow collection decided what every
 * route displayed. Each page owns its own loading and error states now. */
import React from "react";
import { TransactionsContext, useTransactions } from "./use-transactions";

export const TransactionsProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <TransactionsContext.Provider value={useTransactions()}>
    {children}
  </TransactionsContext.Provider>
);
