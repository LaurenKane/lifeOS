/**
 * Route tree / stub page rendering.
 *
 * These tests mount the real route tree from src/routes with a memory
 * router and assert that the stub feature pages actually render. The
 * feature hooks call the API on mount, so fetch is stubbed globally to
 * return an empty collection: these tests must not touch the network or
 * require a running backend.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Routes } from "@/routes";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";

/** Mount the full provider + route tree at the given path. */
function renderAt(path: string) {
  const router = createMemoryRouter(Routes, { initialEntries: [path] });
  return render(
    <TransactionsProvider>
      <ReviewProvider>
        <ImportsProvider>
          <BudgetsProvider>
            <RouterProvider router={router} />
          </BudgetsProvider>
        </ImportsProvider>
      </ReviewProvider>
    </TransactionsProvider>,
  );
}

/** Stub fetch with an empty JSON array so every collection load resolves. */
function stubFetchEmpty() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
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
    ["/finance/transactions", "Transactions", /no transactions yet/i],
    ["/finance/review", "Review Queue", /no items in review queue/i],
    ["/finance/imports", "Imports", /no import batches/i],
    ["/finance/budgets", "Budgets", /no budgets defined/i],
  ])("renders the %s stub page", async (path, heading, emptyText) => {
    renderAt(path);

    // The providers gate on loading, so wait past that first paint.
    await waitFor(() => expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument());
    expect(screen.getByText(emptyText)).toBeInTheDocument();
  });

  it("shows a loading state while the first fetch is in flight", async () => {
    // Never-resolving fetch: the providers must stay in their loading branch.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => new Promise(() => {}) }),
    );

    renderAt("/finance/budgets");

    // The providers are nested, so the outermost (Transactions) gates first
    // and short-circuits its children until its own load settles.
    expect(await screen.findByText(/loading transactions/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Budgets" })).not.toBeInTheDocument();
  });

  it("recovers from a failed fetch instead of hanging on loading", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve("boom"),
      }),
    );

    renderAt("/finance/budgets");

    // The hook catches the error and falls back to an empty collection,
    // so the page renders its empty state rather than an unhandled rejection.
    expect(
      await screen.findByRole("heading", { name: "Budgets" }, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/no budgets defined/i)).toBeInTheDocument();
  });

  it("issues one request per feature collection", async () => {
    const fetchMock = stubFetchEmpty();
    renderAt("/finance/budgets");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Budgets" })).toBeInTheDocument());

    const paths = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(paths).toContain("/api/finance/budgets");
    // The provider owns the fetch; the page must not re-request the same data.
    expect(paths.filter((p) => p === "/api/finance/budgets")).toHaveLength(1);
  });
});
