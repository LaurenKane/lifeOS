/* Transactions page — stub component. */
import React from "react";
import { useTransactionsContext } from "./use-transactions";

export const TransactionsPage: React.FC = () => {
  const { data, loading } = useTransactionsContext();

  if (loading) {
    return <p>Loading transactions…</p>;
  }

  return (
    <section className="p-4">
      <h2 className="text-xl font-semibold mb-2">Transactions</h2>
      <p className="text-muted-foreground">
        {data.length === 0 ? "No transactions yet." : `${data.length} transactions.`}
      </p>
    </section>
  );
};
