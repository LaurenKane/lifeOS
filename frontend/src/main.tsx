/* App entry point — mounts the router wrapped in the feature providers.
 * This module is the script referenced by index.html, so it must both
 * export `App` (for tests) and perform the createRoot render. */
import React from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Routes } from "./routes";
import { AccountsProvider } from "@/features/finance/accounts/provider";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";
import "./index.css";

export const App: React.FC = () => {
  const router = createBrowserRouter(Routes);
  return (
    <AccountsProvider>
      <TransactionsProvider>
        <ReviewProvider>
          <ImportsProvider>
            <BudgetsProvider>
              <RouterProvider router={router} />
            </BudgetsProvider>
          </ImportsProvider>
        </ReviewProvider>
      </TransactionsProvider>
    </AccountsProvider>
  );
};

const container = document.getElementById("root");
if (container === null) {
  throw new Error('Missing #root element — index.html must provide <div id="root">');
}

createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
