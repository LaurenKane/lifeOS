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
import { CategoriesProvider } from "@/features/finance/categories/provider";
import "./index.css";

/* The nesting order is alphabetical and has no other meaning: these providers
 * do not read one another, they only wrap the router. `CategoriesProvider` sits
 * outside `TransactionsProvider` because the transactions LIST reads the category
 * names, and a provider that wrapped its own consumer would throw on first
 * render. */
export const App: React.FC = () => {
  const router = createBrowserRouter(Routes);
  return (
    <AccountsProvider>
      <CategoriesProvider>
        <TransactionsProvider>
          <ReviewProvider>
            <ImportsProvider>
              <BudgetsProvider>
                <RouterProvider router={router} />
              </BudgetsProvider>
            </ImportsProvider>
          </ReviewProvider>
        </TransactionsProvider>
      </CategoriesProvider>
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
