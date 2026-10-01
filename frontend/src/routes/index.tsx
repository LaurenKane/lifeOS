/* Routes — wires up the feature routes. */
import type { RouteObject } from "react-router-dom";
import { Root } from "./root";
import { TransactionsPage } from "@/features/finance/transactions/page";
import { ReviewPage } from "@/features/finance/review/page";
import { ImportsPage } from "@/features/finance/imports/page";
import { BudgetsPage } from "@/features/finance/budgets/page";

export const Routes: RouteObject[] = [
  {
    path: "/",
    element: <Root />,
  },
  {
    path: "/finance/transactions",
    element: <TransactionsPage />,
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