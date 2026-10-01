/* Main App component — wires up feature providers and the router. */
import React from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Routes } from "./routes";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";

export const App: React.FC = () => {
  const router = createBrowserRouter(Routes);
  return (
    <TransactionsProvider>
      <ReviewProvider>
        <ImportsProvider>
          <BudgetsProvider>
            <RouterProvider router={router} />
          </BudgetsProvider>
        </ImportsProvider>
      </ReviewProvider>
    </TransactionsProvider>
  );
};