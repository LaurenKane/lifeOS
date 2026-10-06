/**
 * Route tree, and the states every route shares.
 *
 * The feature hooks call the API on mount, so `fetch` is stubbed globally: no
 * test touches the network or needs a running backend.
 *
 * Three assertions here are about honesty rather than rendering, and they used
 * to assert the opposite:
 *
 *   - a page shows ITS OWN loading state, because the providers no longer
 *     short-circuit the whole app while the slowest collection is in flight;
 *   - a failed read shows the server's message instead of falling back to an
 *     empty collection, which is how a backend that is down used to look like
 *     a ledger with nothing in it;
 *   - the paths are `/api/v1/...`, because every router in
 *     `backend/main.py` hangs off `API_V1_PREFIX` = `/api/v1` and the base URL
 *     is `/api`. The old `/api/finance/...` paths were mounted nowhere.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Routes } from "@/routes";
import { AccountsProvider } from "@/features/finance/accounts/provider";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";
import { CategoriesProvider } from "@/features/finance/categories/provider";

/** Mount the full provider + route tree at the given path.
 *
 * The nesting matches `src/main.tsx`, which is the whole point of this helper:
 * a provider tree here that differs from the app's is a test that passes against
 * a shape the product never runs. `CategoriesProvider` sits outside
 * `TransactionsProvider` for the same reason it does there — the transactions
 * list reads the category names. */
function renderAt(path: string) {
  const router = createMemoryRouter(Routes, { initialEntries: [path] });
  return render(
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
    </AccountsProvider>,
  );
}

/** Stub fetch with an empty JSON array so every collection load resolves. */
function stubFetchEmpty() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve([]),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("route tree", () => {
  beforeEach(() => {
    stubFetchEmpty();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the root route", async () => {
    renderAt("/");
    expect(
      await screen.findByRole("heading", { name: /life os/i }),
    ).toBeInTheDocument();
  });

  it.each([
    ["/finance/accounts", "Accounts", /no accounts yet/i],
    ["/finance/transactions", "Transactions", /nothing recorded yet/i],
    ["/finance/review", "Review queue", /nothing in the queue/i],
    ["/finance/categories", "Categories", /no categories at all/i],
    ["/finance/categories/rules", "Categorization rules", /no rules yet/i],
    ["/finance/imports", "Imports", /no import batches/i],
    ["/finance/budgets", "Budgets", /no budgets defined/i],
  ])("renders the %s page", async (path, heading, emptyText) => {
    renderAt(path);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument(),
    );
    expect(screen.getByText(emptyText)).toBeInTheDocument();
  });

  it("shows a loading state on the page being viewed", async () => {
    // Never-resolving fetch: the page owns its loading state, and the routes
    // around it are not held hostage by whichever collection is slowest.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) }),
    );

    renderAt("/finance/transactions");

    expect(await screen.findByRole("heading", { name: "Transactions" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: /loading transactions/i })).toBeInTheDocument();
  });

  it("reports a failed read instead of rendering an empty collection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve(JSON.stringify({ detail: "boom" })),
      }),
    );

    renderAt("/finance/budgets");

    // The old hook swallowed this and fell back to `[]`, so a dead backend was
    // indistinguishable from a user who has never set a budget.
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByText(/no budgets defined/i)).not.toBeInTheDocument();
  });

  it("issues one request per feature collection", async () => {
    const fetchMock = stubFetchEmpty();
    renderAt("/finance/budgets");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Budgets" })).toBeInTheDocument(),
    );

    const paths = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(paths).toContain("/api/v1/budgets");
    // The provider owns the fetch; the page must not re-request the same data.
    expect(paths.filter((p) => p === "/api/v1/budgets")).toHaveLength(1);
  });

  it("renders the 404 route as the overview rather than a blank screen", async () => {
    renderAt("/finance/nothing-here");
    expect(
      await screen.findByRole("heading", { name: /life os/i }),
    ).toBeInTheDocument();
  });
});
