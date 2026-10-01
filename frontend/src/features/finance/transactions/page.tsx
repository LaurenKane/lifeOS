/* Transactions page — stub component. */
import React from "react";
import { useTransactions } from "./use-transactions";

export const TransactionsPage: React.FC = () => {
  const [_transactions, loading] = useTransactions();

  if (loading) {
    return <p>Loading transactions…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Transactions</h2>
      <p className="text-muted-foreground">No transactions yet.</p>
    </section>
  );
};