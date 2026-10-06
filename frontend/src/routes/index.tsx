/* Routes — wires up the feature routes.
 *
 * Flat, as it was: no layout route, no nesting. Every page renders its own
 * <AppShell>, so the navigation lives in one component instead of in the shape
 * of the route tree.
 *
 * `/` is the Overview: the page that opens on the net-worth figure rather than
 * on a greeting. The catch-all still resolves to it, because a URL that matches
 * nothing should land somewhere that says what this application is — and the
 * Overview now does that better than the stub landing page did.
 */
import type { RouteObject } from "react-router-dom";
import { AccountsPage } from "@/features/finance/accounts/page";
import { TransactionsPage } from "@/features/finance/transactions/page";
import { TransactionDetailPage } from "@/features/finance/transactions/detail";
import { ReviewPage } from "@/features/finance/review/page";
import { CategoriesPage } from "@/features/finance/categories/page";
import { RulesPage } from "@/features/finance/categories/rules-page";
import { MerchantsPage } from "@/features/finance/merchants/page";
import { ImportsPage } from "@/features/finance/imports/page";
import { BudgetsPage } from "@/features/finance/budgets/page";
import { OverviewPage } from "@/features/finance/overview/page";

export const Routes: RouteObject[] = [
  {
    path: "/",
    element: <OverviewPage />,
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
    // The two halves of the categorization surface. The rules screen is the
    // editable set and the category screen is the inventory it points at; both
    // are static, so both rank above the `/finance/transactions/:id` segment
    // regardless of the order they are declared in.
    path: "/finance/categories/rules",
    element: <RulesPage />,
  },
  {
    path: "/finance/categories",
    element: <CategoriesPage />,
  },
  {
    // The third half of the categorization surface, and the one that makes
    // layers 2-4 reachable: the merchant and alias tables the matcher reads.
    // Declared beside the other two curation routes because they are three
    // addresses for one subject, and all three are static.
    path: "/finance/merchants",
    element: <MerchantsPage />,
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
    element: <OverviewPage />,
  },
];