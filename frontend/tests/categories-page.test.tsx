/**
 * Categories: what a transaction can be filed under, and making one more.
 *
 * The behaviours asserted here are the ones that would make this page lie:
 *
 *   1. THE GROUPING IS BY KIND AND THE PAGE SAYS WHY. `CategorySummary` does not
 *      carry `parent_id`, so the screen cannot draw a tree and does not pretend
 *      to. The test asserts the caveat is stated, because a page that quietly
 *      showed a flat list under a heading implying hierarchy would be a
 *      fabricated tree on a finance screen.
 *
 *   2. SYSTEM CATEGORIES ARE VISIBLE AND MARKED. They are the ones a transaction
 *      can be filed under, so hiding them makes the page wrong — but they carry
 *      no edit affordance, because there is no endpoint to rename one.
 *
 *   3. A REFUSED CREATE SHOWS THE SERVER'S WORDS. A 409 for a duplicate is the
 *      most likely failure on this form and the most confusing one to swallow.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Routes } from "@/routes";
import { AccountsProvider } from "@/features/finance/accounts/provider";
import { CategoriesProvider } from "@/features/finance/categories/provider";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";

const GROCERIES = { id: 12, name: "Groceries", kind: "expense", is_system: true } as const;
const SALARY = { id: 40, name: "Salary", kind: "income", is_system: true } as const;
const TRANSFER = { id: 55, name: "Own transfer", kind: "transfer", is_system: false } as const;
const CATEGORIES = [GROCERIES, SALARY, TRANSFER];

const stubApi = (
  handlers: Record<string, (init?: RequestInit) => { status: number; body: unknown }>,
) => {
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  const fetchMock = vi.fn((input: unknown, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const key = `${method} ${path.replace("/api/v1", "").replace("/api", "")}`;
    const handler = handlers[key];
    calls.push({ path, method, body: init?.body });
    if (handler === undefined) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    const { status, body } = handler(init);
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
};

const baseHandlers = (
  overrides: Record<string, (init?: RequestInit) => { status: number; body: unknown }> = {},
) => ({
  "GET /categories": () => ({ status: 200, body: CATEGORIES }),
  "GET /categories/kinds": () => ({ status: 200, body: ["expense", "income", "transfer", "investment"] }),
  "GET /categories/rules": () => ({ status: 200, body: [] }),
  "GET /accounts": () => ({ status: 200, body: [] }),
  "GET /transactions": () => ({ status: 200, body: [] }),
  "GET /review/transfers": () => ({ status: 200, body: [] }),
  "GET /review/transfers/stats": () => ({ status: 200, body: { multi_candidate: 0, low_confidence: 0, total: 0 } }),
  ...overrides,
});

const renderAt = (path: string) => {
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
};

const postBodies = (
  calls: Array<{ method: string; body: unknown }>,
): Array<Record<string, unknown>> =>
  calls
    .filter((call) => call.method === "POST")
    .map((call) =>
      typeof call.body === "string"
        ? (JSON.parse(call.body) as Record<string, unknown>)
        : ({} as Record<string, unknown>),
    );

describe("categories — reading the list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("groups by kind and states that a parent tree is not available", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories");

    expect(await screen.findByRole("heading", { name: "Categories" })).toBeInTheDocument();

    // Each kind is its own section, headed by what the kind DOES — "Expense"
    // alone says nothing; "a transaction in this category reduces the balance"
    // is the sentence a reader needs before choosing.
    const groups = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(groups).toEqual(["Expense", "Income", "Transfer"]);

    // The caveat is stated, not implied away.
    expect(
      screen.getByText(/grouped by kind, not by parent/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/no read endpoint returns that link/i),
    ).toBeInTheDocument();
  });

  it("lists every category, marking the ones that ship with the ledger", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories");

    await screen.findByText("Groceries");
    for (const name of ["Groceries", "Salary", "Own transfer"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }

    // System rows say so in words. A grey row would only say "different".
    const systemRow = screen.getByText("Groceries").closest("li");
    expect(within(systemRow as HTMLElement).getByText(/ships with the ledger/i)).toBeInTheDocument();

    const ownRow = screen.getByText("Own transfer").closest("li");
    expect(within(ownRow as HTMLElement).queryByText(/ships with the ledger/i)).toBeNull();

    // And no edit affordance, because there is no endpoint to rename one.
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });

  it("reports a failed read instead of an empty ledger", async () => {
    stubApi(
      baseHandlers({
        "GET /categories": () => ({ status: 500, body: { detail: "categories unavailable" } }),
      }),
    );
    renderAt("/finance/categories");

    expect(await screen.findByText("categories unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/no categories at all/i)).not.toBeInTheDocument();
    /* And the create form says why it is closed, without repeating the
       server's message in a second red box. */
    expect(screen.getByText(/this form is closed/i)).toBeInTheDocument();
  });
});

describe("categories — creating one", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a category with its name and kind", async () => {
    let created: Record<string, unknown> | null = null;
    const calls = stubApi(
      baseHandlers({
        "POST /categories": (init) => {
          created = JSON.parse(String(init?.body));
          return {
            status: 201,
            body: { id: 77, ...created, is_system: false },
          };
        },
      }),
    );
    renderAt("/finance/categories");

    await userEvent.type(await screen.findByLabelText(/^name$/i), "Coffee");
    await userEvent.selectOptions(screen.getByLabelText(/^kind$/i), "expense");
    await userEvent.click(screen.getByRole("button", { name: /create the category/i }));

    await waitFor(() => {
      expect(created).not.toBeNull();
    });
    /* No `parent_id` when the parent field was left alone: an absent field means
       top level, and sending an explicit null would be a different request. */
    expect(created).toEqual({ name: "Coffee", kind: "expense" });
    expect(postBodies(calls)).toHaveLength(1);

    /* Named by what it created, and the id spelled out — the id is what a user
       needs to check the rule they just pointed at it. */
    expect(
      await screen.findByText(/Coffee is category/i, { exact: false }),
    ).toBeInTheDocument();
  });

  it("sends the parent when one is chosen", async () => {
    let created: Record<string, unknown> | null = null;
    stubApi(
      baseHandlers({
        "POST /categories": (init) => {
          created = JSON.parse(String(init?.body));
          return { status: 201, body: { id: 78, ...created, is_system: false } };
        },
      }),
    );
    renderAt("/finance/categories");

    await userEvent.type(await screen.findByLabelText(/^name$/i), "Filter coffee");
    await userEvent.selectOptions(screen.getByLabelText(/^parent category$/i), String(GROCERIES.id));
    await userEvent.click(screen.getByRole("button", { name: /create the category/i }));

    await waitFor(() => {
      expect(created).not.toBeNull();
    });
    expect(created).toEqual({ name: "Filter coffee", kind: "expense", parent_id: GROCERIES.id });
  });

  it("keeps the button closed until there is a name", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/categories");

    expect(await screen.findByRole("button", { name: /create the category/i })).toBeDisabled();
    expect(postBodies(calls)).toHaveLength(0);
  });

  it("shows the server's words when the name is a duplicate", async () => {
    stubApi(
      baseHandlers({
        "POST /categories": () => ({
          status: 409,
          body: {
            detail: "A category with that parent and name already exists: unique_violation",
          },
        }),
      }),
    );
    renderAt("/finance/categories");

    await userEvent.type(await screen.findByLabelText(/^name$/i), "Groceries");
    await userEvent.click(screen.getByRole("button", { name: /create the category/i }));

    expect(
      await screen.findByText(/A category with that parent and name already exists/),
    ).toBeInTheDocument();
  });

  it("closes the form when the category list could not be read", async () => {
    stubApi(
      baseHandlers({
        "GET /categories": () => ({ status: 500, body: { detail: "categories unavailable" } }),
      }),
    );
    renderAt("/finance/categories");

    await screen.findByText("categories unavailable");
    // The NAME box stays usable — an incomplete form is not a disabled form —
    // but the write cannot proceed without a readable list.
    expect(screen.getByRole("button", { name: /create the category/i })).toBeDisabled();
    expect(screen.getByLabelText(/^kind$/i)).toBeDisabled();
  });
});