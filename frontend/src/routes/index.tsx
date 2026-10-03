/* Routes — wires up the feature routes.
 *
 * Flat, as it was: no layout route, no nesting. Every page renders its own
 * <AppShell>, so the navigation lives in one component instead of in the shape
 * of the route tree. */
import type { RouteObject } from "react-router-dom";
import { Root } from "./root";
import { AccountsPage } from "@/features/finance/accounts/page";
import { TransactionsPage } from "@/features/finance/transactions/page";
import { TransactionDetailPage } from "@/features/finance/transactions/detail";
import { ReviewPage } from "@/features/finance/review/page";
import { ImportsPage } from "@/features/finance/imports/page";
import { BudgetsPage } from "@/features/finance/budgets/page";

export const Routes: RouteObject[] = [
  {
    path: "/",
    element: <Root />,
  },
  {
    path: "/finance/accounts",
    element: <AccountsPage />,
  },
  {
    path: "/finance/transactions",
    element: <TransactionsPage />,
  },
  {
    // After `/finance/transactions` and before the catch-all. React Router ranks
    // static segments above dynamic ones regardless of order, but a static
    // route declared after a dynamic one is the kind of thing that only breaks
    // on the next reordering.
    path: "/finance/transactions/:id",
    element: <TransactionDetailPage />,
  },
  {
    path: "/finance/review",
    element: <ReviewPage />,
  },
  {
    path: "/finance/imports",
    element: <ImportsPage />,
  },
  {
    path: "/finance/budgets",
    element: <BudgetsPage />,
  },
  // Catch-all 404
  {
    path: "*",
    element: <Root />,
  },
];
