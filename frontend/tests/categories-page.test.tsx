/**
 * Categories: what a transaction can be filed under, and making one more.
 *
 * The behaviours asserted here are the ones that would make this page lie:
 *
 *   1. THE TREE IS DRAWN FROM `parent_id`, INSIDE THE KIND SECTIONS. `kind`
 *      decides a balance's sign, so it is what a section is named after, and
 *      the parent nests within one. The tests assert a child renders under its
 *      parent, and assert the page says why a parent and a child may be of
 *      different kinds rather than quietly re-homing the child.
 *
 *      The seed data here has no parent, so the nesting assertions bring their
 *      own categories — which is also the point: a flat list must not be
 *      mistaken for a working tree just because the shipped categories are flat.
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

const GROCERIES = { id: 12, name: "Groceries", kind: "expense", is_system: true, parent_id: null } as const;
const SALARY = { id: 40, name: "Salary", kind: "income", is_system: true, parent_id: null } as const;
const TRANSFER = { id: 55, name: "Own transfer", kind: "transfer", is_system: false, parent_id: null } as const;
/** A child of Groceries, same kind. This is what `parent_id` is for, and it is
 * absent from the shipped set above on purpose: a page that only ever renders
 * top-level rows would pass every other assertion on this file. */
const ORGANIC = { id: 78, name: "Organic", kind: "expense", is_system: false, parent_id: 12 } as const;
const CATEGORIES = [GROCERIES, SALARY, TRANSFER];
const NESTED = [...CATEGORIES, ORGANIC];

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

  it("keeps kind as the section and nests by parent inside it", async () => {
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: NESTED }) }));
    renderAt("/finance/categories");

    expect(await screen.findByRole("heading", { name: "Categories" })).toBeInTheDocument();

    // Each kind is still its own section, headed by what the kind DOES — "Expense"
    // alone says nothing; "a transaction in this category reduces the balance"
    // is the sentence a reader needs before choosing. `kind` decides a balance's
    // sign, so it stays the rank the tree hangs from.
    const groups = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(groups).toEqual(["Expense", "Income", "Transfer"]);

    // The child is INSIDE the parent's own list item, not merely later in the
    // document: a child that renders as a sibling with an indent would look the
    // same to a reader and mean something different.
    const parent = (await screen.findByText("Groceries")).closest("li");
    expect(parent).not.toBeNull();
    expect(within(parent as HTMLElement).getByText("Organic")).toBeInTheDocument();
    expect(within(parent as HTMLElement).getByText(/1 under it/i)).toBeInTheDocument();

    // And the parent is a disclosure that starts open, so nothing in the tree is
    // hidden by default. A folded category is a category the reader cannot see.
    const summary = within(parent as HTMLElement).getByText("Groceries").closest("summary");
    expect(summary).not.toBeNull();
    expect((summary as HTMLElement).closest("details")).toHaveAttribute("open");
  });

  it("draws deeper levels as further nested lists", async () => {
    /* Three levels, because one level could be an indent and three is a tree. */
    const deep = [
      ...CATEGORIES,
      ORGANIC,
      { id: 91, name: "Bakery", kind: "expense", is_system: false, parent_id: 78 },
    ];
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: deep }) }));
    renderAt("/finance/categories");

    const level1 = (await screen.findByText("Organic")).closest("li");
    expect(level1).not.toBeNull();
    // One list inside another, not a paragraph of dashes.
    const level2 = within(level1 as HTMLElement).getByText("Bakery").closest("li");
    expect(level2).not.toBeNull();
    expect(
      (level2 as HTMLElement).closest("ul") !== within(level1 as HTMLElement).getByText("Organic").closest("ul"),
    ).toBe(true);
    expect(within(level1 as HTMLElement).getByText(/1 under it/i)).toBeInTheDocument();
  });

  it("leaves a leaf row as a plain row with no control on it", async () => {
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: NESTED }) }));
    renderAt("/finance/categories");

    const child = (await screen.findByText("Organic")).closest("li");
    expect(child).not.toBeNull();
    /* A disclosure with nothing to disclose is a control that does nothing. */
    expect((child as HTMLElement).querySelector("summary")).toBeNull();
    expect((child as HTMLElement).querySelector("button")).toBeNull();
  });

  it("names a parent of another kind instead of drawing the link across sections", async () => {
    /* The server does not require a child's kind to match its parent's, so this
       is reachable data rather than a corner case. An income row drawn under an
       expense parent would put it in the wrong section for a sign the ledger
       computes. */
    const crossed = [
      ...CATEGORIES,
      { id: 64, name: "Freelance", kind: "income", is_system: false, parent_id: 12 },
    ];
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: crossed }) }));
    renderAt("/finance/categories");

    // It is listed under Income, not under Groceries in the Expense section.
    const income = (await screen.findByRole("heading", { level: 4, name: "Income" }))
      .closest("section");
    const freelance = within(income as HTMLElement).getByText("Freelance");
    expect(freelance).toBeInTheDocument();

    // And it says which parent it belongs to, so the hierarchy is not silently
    // disagreeing with the ledger.
    const row = freelance.closest("li");
    expect(within(row as HTMLElement).getByText(/filed under/i)).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Groceries")).toBeInTheDocument();

    // The page states the rule rather than leaving it to be inferred.
    expect(screen.getByText(/a parent and its child can be different kinds/i)).toBeInTheDocument();
  });

  it("says so when a parent_id names no category the page could read", async () => {
    const dangling = [
      ...CATEGORIES,
      { id: 66, name: "Ghost branch", kind: "expense", is_system: false, parent_id: 999 },
    ];
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: dangling }) }));
    renderAt("/finance/categories");

    const row = (await screen.findByText("Ghost branch")).closest("li");
    expect(row).not.toBeNull();
    /* It is shown, not dropped — every category in the list is on this page. */
    expect(within(row as HTMLElement).getByText(/not among the categories this page could read/i)).toBeInTheDocument();
  });

  it("does not loop on a parent_id cycle", async () => {
    /* `parent_id` is a plain self-reference and nothing forbids closing a cycle,
       so a recursive walk over one never returns. The row has to render. */
    const cyclic = [
      ...CATEGORIES,
      { id: 70, name: "A", kind: "expense", is_system: false, parent_id: 71 },
      { id: 71, name: "B", kind: "expense", is_system: false, parent_id: 70 },
    ];
    stubApi(baseHandlers({ "GET /categories": () => ({ status: 200, body: cyclic }) }));
    renderAt("/finance/categories");

    /* Both are listed, each once. A walk that recursed into the cycle would hang
       or repeat rows; one that gave up on the pair would drop two categories the
       user has. Scoped to the panel, because the create form's parent picker
       offers the same names as `<option>` text. */
    const panel = (await screen.findByRole("heading", { name: "Every category" })).closest(
      "section",
    );
    expect(within(panel as HTMLElement).getByText("A")).toBeInTheDocument();
    expect(within(panel as HTMLElement).getByText("B")).toBeInTheDocument();
    expect(within(panel as HTMLElement).getAllByText("A")).toHaveLength(1);
    expect(within(panel as HTMLElement).getAllByText("B")).toHaveLength(1);
    /* And it says why, rather than showing a top-level row that silently claims
       to have no parent. Once, on the row that sits at the top — the other is
       drawn beneath it, which is where its own `parent_id` says it belongs. */
    expect(
      within(panel as HTMLElement).getAllByText(/lists this category as ITS parent/i),
    ).toHaveLength(1);
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

  it("sends the parent when one is chosen, and the new row nests under it", async () => {
    let created: Record<string, unknown> | null = null;
    stubApi(
      baseHandlers({
        "POST /categories": (init) => {
          created = JSON.parse(String(init?.body));
          return {
            status: 201,
            body: { id: 88, ...created, is_system: false, parent_id: GROCERIES.id },
          };
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

    /* The created row comes back with `parent_id` — the server's own `CategorySummary`
       — and it lands inside its parent's branch rather than at the top of the
       section. Before `parent_id` was returned this was impossible to check: the
       screen could only append the row and hope. */
    const parent = (await screen.findByText("Groceries")).closest("li");
    expect(parent).not.toBeNull();
    expect(within(parent as HTMLElement).getByText("Filter coffee")).toBeInTheDocument();
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