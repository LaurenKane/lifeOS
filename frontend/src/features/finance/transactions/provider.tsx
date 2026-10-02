/* Transactions provider — stub. Owns the single transactions fetch and
 * exposes the result to pages via TransactionsContext.
 */
import React from "react";
import { TransactionsContext, useTransactions } from "./use-transactions";

export const TransactionsProvider: React.FC<{
  children: React.ReactNode;
}> = ({ children }) => {
  const state = useTransactions();

  if (state.loading) {
    return <p>Loading transactions…</p>;
  }

  return (
    <TransactionsContext.Provider value={state}>
      <div>{children}</div>
    </TransactionsContext.Provider>
  );
};
