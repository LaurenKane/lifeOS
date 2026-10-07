/* Routes — wires up the feature routes.
 *
 * Flat, as it was: no layout route, no nesting. Every page renders its own
 * `<AppShell>`, so the navigation lives in one component instead of in the
 * shape of the route tree.
 *
 * `/` IS THE LIFE DASHBOARD, and the catch-all resolves to it too — docs/adr/
 * 0012-dashboard-app-root.md. A URL that matches nothing lands on the screen
 * that says what this application is, and since discovery concluded the app
 * opens on a phone on a bad-brain day, that screen is Today: the do-now list,
 * the pile, upkeep, vision, and the money figures read live from finance. The
 * finance overview it displaced moved one door to the right, to
 * `/finance/overview`, and every other `/finance/*` address is unchanged.
 *
 * The `/life/*` pages are the module's own detail views (ADR 0010's prefix
 * rule), declared as static segments beside the finance block: they are one
 * word each, they never overlap, and there is no dynamic segment anywhere in
 * this table for them to outrank.
 */
import type { RouteObject } from "react-router-dom";
import { DashboardPage } from "@/features/life/dashboard/page";
import { InboxPage } from "@/features/life/inbox/page";
import { GoalsPage } from "@/features/life/goals/page";
import { UpkeepPage } from "@/features/life/upkeep/page";
import { VisionPage } from "@/features/life/vision/page";
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
    // The app root is the life Dashboard (ADR 0012), not the finance overview.
    path: "/",
    element: <DashboardPage />,
  },
  {
    // The life module's own screens, one address each. The pile, the Goals,
    // the recurring things, and the why — the four views the Dashboard links
    // into rather than trying to be on its own.
    path: "/life/inbox",
    element: <InboxPage />,
  },
  {
    path: "/life/goals",
    element: <GoalsPage />,
  },
  {
    path: "/life/upkeep",
    element: <UpkeepPage />,
  },
  {
    path: "/life/vision",
    element: <VisionPage />,
  },
  {
    // Where `/` used to be. Nothing else in finance moved: this is one address
    // changing hands, not a reorganisation of the module.
    path: "/finance/overview",
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
  // Catch-all 404 — the Dashboard, because an address that matches nothing
  // should still open on what this application is (ADR 0012).
  {
    path: "*",
    element: <DashboardPage />,
  },
];
