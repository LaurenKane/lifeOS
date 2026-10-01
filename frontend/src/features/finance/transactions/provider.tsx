/* Transactions provider — stub. Real implementation will wrap
 * useTransactions and provide the context for the feature.
 */
import React from "react";
import { useTransactions } from "./use-transactions";

export const TransactionsProvider: React.FC<{
  children: React.ReactNode;
}> = ({ children }) => {
  const { data: transactions, loading } = useTransactions();

  if (loading) {
    return <p>Loading transactions…</p>;
  }

  return <div>{children}</div>;
};