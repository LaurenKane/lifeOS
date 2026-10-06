/**
 * The merchants screen, against a stubbed API.
 *
 * The behaviours here are the ones a wrong implementation gets wrong in the
 * direction that hides work, and none of them are about styling:
 *
 *   1. AN UNFILED ROW IS LISTED AND SAYS IT MATCHES NOTHING. `load_known_merchants`
 *      and `load_aliases` both skip a row with no `category_id`, so a merchant
 *      stored without one is inert. A screen that hid those rows, or listed them
 *      as though they were working, would leave a user believing they had taught
 *      the ledger a name when they had not.
 *
 *   2. GROUPING IS BY THE KIND OF THE CATEGORY, THE SAME ANSWER THE PICKER
 *      GIVES. A flat alphabetical list would hide that "Savings" is a transfer and
 *      not an investment, which is the distinction that decides a balance's sign.
 *
 *   3. DELETING A MERCHANT DELETES ITS ALIASES, AND THE COUNT IS NAMED BEFORE
 *      ANYTHING IS SENT. `merchant_alias.merchant_id` is ON DELETE CASCADE, so
 *      one row's delete can remove several the user did not click. A refused
 *      delete leaves the row exactly where it was.
 *
 *   4. FILING A MERCHANT IS A PATCH THAT SENDS THE KEY EVEN WHEN CLEARING.
 *      `model_fields_set` is what makes `{"category_id": null}` a clear rather
 *      than a no-op, so a client that dropped the key would report a clearing
 *      that never reached the ledger.
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
import { MerchantsProvider } from "@/features/finance/merchants/provider";

const GROCERIES = { id: 12, name: "Groceries", kind: "expense", is_system: true } as const;
const MUSIC = { id: 31, name: "Music", kind: "expense", is_system: false } as const;
const SALARY = { id: 40, name: "Salary", kind: "income", is_system: true } as const;
const CATEGORIES = [GROCERIES, MUSIC, SALARY];

/** Filed at Groceries — an expense. */
const FILED_MERCHANT = { id: 4, name: "Albert Heijn", category_id: GROCERIES.id } as const;

/** Filed at Salary — an income. A different KIND, so a screen grouping only by
 * kind would put it in a different section from the row above. */
const INCOME_MERCHANT = { id: 5, name: "Bakker", category_id: SALARY.id } as const;

/** Known but unfiled: stored, read back faithfully, and matching nothing at
 * either layer 3 or layer 4. */
const UNFILED_MERCHANT = { id: 6, name: "Ziggo", category_id: null } as const;

const MERCHANTS = [FILED_MERCHANT, INCOME_MERCHANT, UNFILED_MERCHANT];

/** At the bar: files without asking. */
const AUTO_ALIAS = {
  id: 8,
  raw_string: "ALBERTHEIJN 1234 AMSTERDAM",
  merchant_id: FILED_MERCHANT.id,
  category_id: GROCERIES.id,
  confidence: "1.00",
} as const;

/** Below the bar: the same match goes to the review queue instead. The two
 * together are why confidence is printed on every alias row. */
const UNSURE_ALIAS = {
  id: 9,
  raw_string: "TNT 4588",
  merchant_id: null,
  category_id: MUSIC.id,
  confidence: "0.60",
} as const;

const ALIASES = [AUTO_ALIAS, UNSURE_ALIAS];

/** A fetch stub that answers per URL and method, so one test can have a read
 * succeed and a write refuse. The same shape `categories-rules.test.tsx` uses;
 * unhandled paths answer with an empty collection. */
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
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      });
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

/** The reads every page needs, so a test only states the part it is about. */
const baseHandlers = (
  overrides: Record<string, (init?: RequestInit) => { status: number; body: unknown }> = {},
) => ({
  "GET /categories": () => ({ status: 200, body: CATEGORIES }),
  "GET /categories/kinds": () => ({
    status: 200,
    body: ["expense", "income", "transfer", "investment"],
  }),
  "GET /categories/rules": () => ({ status: 200, body: [] }),
  "GET /merchants": () => ({ status: 200, body: MERCHANTS }),
  "GET /merchant-aliases": () => ({ status: 200, body: ALIASES }),
  "GET /accounts": () => ({ status: 200, body: [] }),
  "GET /transactions": () => ({ status: 200, body: [] }),
  "GET /review/transfers": () => ({ status: 200, body: [] }),
  "GET /review/transfers/stats": () => ({
    status: 200,
    body: { multi_candidate: 0, low_confidence: 0, total: 0 },
  }),
  ...overrides,
});

/** The provider nesting matches `src/main.tsx`, which is the whole point of the
 * helper: `MerchantsProvider` sits inside `CategoriesProvider` because the screen
 * groups every row by the kind of the category it points at. A tree that differed
 * from the app's would be a test passing against a shape the product never runs. */
const renderAt = (path: string) => {
  const router = createMemoryRouter(Routes, { initialEntries: [path] });
  return render(
    <AccountsProvider>
      <CategoriesProvider>
        <TransactionsProvider>
          <ReviewProvider>
            <ImportsProvider>
              <BudgetsProvider>
                <MerchantsProvider>
                  <RouterProvider router={router} />
                </MerchantsProvider>
              </BudgetsProvider>
            </ImportsProvider>
          </ReviewProvider>
        </TransactionsProvider>
      </CategoriesProvider>
    </AccountsProvider>,
  );
};

/** The panel a heading belongs to.
 *
 * Scoping matters here in a way it does not on the rules screen: the alias form's
 * merchant dropdown lists every stored merchant by name, so an unscoped
 * `getByText("Albert Heijn")` finds the row AND an `<option>`. The panel is
 * found by heading role, which is why the panel titles are distinct words from
 * the page title — see `merchants/page.tsx`. */
const panel = (title: string | RegExp): HTMLElement => {
  const heading = screen.getByRole("heading", { name: title });
  const section = heading.closest("section");
  if (section === null) {
    throw new Error(`No panel around the heading ${String(title)}`);
  }
  return section as HTMLElement;
};

const MERCHANTS_PANEL = "Taught names";
const ALIASES_PANEL = "Raw strings banks send";

/** The calls a test made, with their bodies parsed — what the server would have
 * received is the string on the wire. */
const callsTo = (
  calls: Array<{ method: string; path: string; body: unknown }>,
  method: string,
): Array<{ path: string; body: Record<string, unknown> | null }> =>
  calls
    .filter((call) => call.method === method)
    .map((call) => ({
      path: call.path,
      body:
        typeof call.body === "string"
          ? (JSON.parse(call.body) as Record<string, unknown>)
          : null,
    }));

describe("merchants — reading", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists every merchant with the category it is filed under", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    // All three are present at once. No filter, no pagination: a name the user
    // cannot see is a name they will not fix.
    const filed = await within(panel(MERCHANTS_PANEL)).findByText("Albert Heijn");
    expect(filed).toBeInTheDocument();
    expect(within(panel(MERCHANTS_PANEL)).getByText("Bakker")).toBeInTheDocument();
    expect(within(panel(MERCHANTS_PANEL)).getByText("Ziggo")).toBeInTheDocument();

    // Read as a sentence: the name, then what it points at.
    const row = filed.closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Groceries")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("id 12")).toBeInTheDocument();
  });

  it("groups by the kind of the category, not alphabetically across the page", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });
    const expense = within(panel(MERCHANTS_PANEL)).getByText("Albert Heijn");
    const income = within(panel(MERCHANTS_PANEL)).getByText("Bakker");

    // "Bakker" sorts before "Albert Heijn", so an alphabetical page would put it
    // first. Grouping by kind puts the expense row first regardless.
    const expenseSection = expense.closest("section");
    const incomeSection = income.closest("section");
    expect(expenseSection).not.toBeNull();
    expect(incomeSection).not.toBeNull();
    expect(expenseSection).not.toBe(incomeSection);

    // And each section is headed by its kind, in words.
    expect(within(expenseSection as HTMLElement).getByText("Expense")).toBeInTheDocument();
    expect(within(incomeSection as HTMLElement).getByText("Income")).toBeInTheDocument();
  });

  it("says in words that an unfiled merchant matches nothing", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    const unfiled = await within(panel(MERCHANTS_PANEL)).findByText("Ziggo");
    const row = unfiled.closest("li");

    // The consequence, not just an arrow pointing at nothing. `load_known_merchants`
    // skips any merchant with no category, so this row is inert and the user has
    // to be able to see that from the row.
    expect(
      within(row as HTMLElement).getByText(/matches nothing/i),
    ).toBeInTheDocument();

    // And it is grouped apart from the filed ones, under a header saying so.
    const group = unfiled.closest("section");
    expect(within(group as HTMLElement).getByText("Not filed")).toBeInTheDocument();
  });

  it("lists every alias with its raw string and its confidence", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    const raw = await within(panel(ALIASES_PANEL)).findByText("ALBERTHEIJN 1234 AMSTERDAM");
    const row = raw.closest("li");
    expect(row).not.toBeNull();

    expect(within(row as HTMLElement).getByText(/confidence 1\.00/)).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Groceries")).toBeInTheDocument();

    // Below the bar the words change, because the number changes what happens at
    // the next match. `is_auto` is `confidence >= 0.90`.
    const unsure = within(panel(ALIASES_PANEL)).getByText("TNT 4588");
    const unsureRow = unsure.closest("li");
    expect(
      within(unsureRow as HTMLElement).getByText(/review queue/i),
    ).toBeInTheDocument();
  });

  it("reports a failed read instead of rendering an empty collection", async () => {
    stubApi(
      baseHandlers({
        "GET /merchants": () => ({ status: 500, body: { detail: "boom" } }),
      }),
    );
    renderAt("/finance/merchants");

    // The old hook shape swallowed this and fell back to `[]`, so a dead backend
    // was indistinguishable from a ledger nobody has curated.
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByText(/no merchants yet/i)).not.toBeInTheDocument();
  });

  it("degrades to two groups when the categories cannot be read", async () => {
    stubApi(
      baseHandlers({
        "GET /categories": () => ({ status: 500, body: { detail: "categories gone" } }),
      }),
    );
    renderAt("/finance/merchants");

    // Said once, above both panels, because it is one fact about a list both read.
    expect(await screen.findByText("categories gone")).toBeInTheDocument();

    // The screen is still readable and still shows the rows: a delete is
    // addressed by id and needs no category name.
    expect(within(panel(MERCHANTS_PANEL)).getByText("Albert Heijn")).toBeInTheDocument();
    // And the kind groups are gone rather than shown empty.
    expect(within(panel(MERCHANTS_PANEL)).queryByText("Income")).not.toBeInTheDocument();
  });
});

describe("merchants — filing a merchant", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends a PATCH carrying the category id", async () => {
    const calls = stubApi(
      baseHandlers({
        [`PATCH /merchants/${UNFILED_MERCHANT.id}`]: () => ({
          status: 200,
          body: { id: UNFILED_MERCHANT.id, name: UNFILED_MERCHANT.name, category_id: GROCERIES.id },
        }),
      }),
    );
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Ziggo")).closest("li") as HTMLElement;
    await userEvent.selectOptions(
      within(row).getByLabelText(/category for Ziggo/i),
      String(GROCERIES.id),
    );

    await waitFor(() => {
      expect(callsTo(calls, "PATCH")).toEqual([
        {
          path: `/api/v1/merchants/${UNFILED_MERCHANT.id}`,
          body: { category_id: GROCERIES.id },
        },
      ]);
    });
  });

  it("clears the category by sending an explicit null, not by omitting the key", async () => {
    const calls = stubApi(
      baseHandlers({
        [`PATCH /merchants/${FILED_MERCHANT.id}`]: () => ({
          status: 200,
          body: { id: FILED_MERCHANT.id, name: FILED_MERCHANT.name, category_id: null },
        }),
      }),
    );
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Albert Heijn")).closest("li") as HTMLElement;
    await userEvent.selectOptions(
      within(row).getByLabelText(/category for Albert Heijn/i),
      "",
    );

    await waitFor(() => {
      const patches = callsTo(calls, "PATCH");
      expect(patches).toHaveLength(1);
      /* The key PRESENT with a null value is the whole clear. The server reads
       * `model_fields_set`, so an omitted key would leave the row alone while this
       * screen reported it had been unfiled. */
      expect(patches[0]?.body).toEqual({ category_id: null });
      expect(Object.prototype.hasOwnProperty.call(patches[0]?.body ?? {}, "category_id")).toBe(
        true,
      );
    });
  });

  it("leaves the row on screen and reports the server's words when a filing is refused", async () => {
    stubApi(
      baseHandlers({
        [`PATCH /merchants/${UNFILED_MERCHANT.id}`]: () => ({
          status: 404,
          body: { detail: "No category 999" },
        }),
      }),
    );
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Ziggo")).closest("li") as HTMLElement;
    await userEvent.selectOptions(
      within(row).getByLabelText(/category for Ziggo/i),
      String(MUSIC.id),
    );

    // The server's own words, not a generic failure.
    expect(await screen.findAllByText(/No category 999/)).not.toHaveLength(0);
    // And the row did not move: a picker that showed a new value after a refused
    // write would be claiming a filing the ledger does not have.
    expect(within(row).getByText(/matches nothing/i)).toBeInTheDocument();
  });
});

describe("merchants — creating", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a merchant and a category in one request", async () => {
    const calls = stubApi(
      baseHandlers({
        "POST /merchants": () => ({
          status: 201,
          body: { id: 77, name: "Jumbo", category_id: GROCERIES.id },
        }),
      }),
    );
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    await userEvent.type(screen.getByLabelText(/merchant name/i), "Jumbo");
    await userEvent.selectOptions(
      screen.getByLabelText(/^category/i, { selector: "#merchant-category" }),
      String(GROCERIES.id),
    );
    await userEvent.click(screen.getByRole("button", { name: /add the merchant/i }));

    await waitFor(() => {
      expect(callsTo(calls, "POST")).toEqual([
        { path: "/api/v1/merchants", body: { name: "Jumbo", category_id: GROCERIES.id } },
      ]);
    });
  });

  it("refuses a duplicate name before it is sent", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    // "Albert Heijn" is already stored, so the 409 the server would raise is
    // caught where it is typed — the button does not offer the request.
    await userEvent.type(screen.getByLabelText(/merchant name/i), "Albert Heijn");

    expect(
      await screen.findByText(/already stored/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add the merchant/i }),
    ).toBeDisabled();
    expect(callsTo(calls, "POST")).toHaveLength(0);
  });

  it("sends an alias confidence as a decimal string, never a float", async () => {
    const calls = stubApi(
      baseHandlers({
        "POST /merchant-aliases": () => ({
          status: 201,
          body: {
            id: 91,
            raw_string: "JUMBO 4321 AMSTERDAM",
            merchant_id: null,
            category_id: GROCERIES.id,
            confidence: "1.00",
          },
        }),
      }),
    );
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    await userEvent.type(screen.getByLabelText(/raw string/i), "JUMBO 4321 AMSTERDAM");
    await userEvent.selectOptions(
      screen.getByLabelText(/^category/i, { selector: "#alias-category" }),
      String(GROCERIES.id),
    );
    await userEvent.click(screen.getByRole("button", { name: /add the alias/i }));

    await waitFor(() => {
      const posts = callsTo(calls, "POST");
      expect(posts).toHaveLength(1);
      /* The column is NUMERIC(3,2) on a Decimal. A JSON float here would put a
       * float next to a money-adjacent value, which this project does not do
       * anywhere. */
      expect(posts[0]?.body?.confidence).toBe("1.00");
      expect(typeof posts[0]?.body?.confidence).toBe("string");
    });
  });

  it("will not submit an alias with neither a category nor a merchant", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    await userEvent.type(screen.getByLabelText(/raw string/i), "TNT 4588");

    // The server refuses this with a 422 — a string pointing at nothing is a
    // question stored as an answer — so the gate is the same condition, and the
    // field hint says which one is missing.
    expect(screen.getByRole("button", { name: /add the alias/i })).toBeDisabled();
    expect(
      screen.getByText(/needs a category or a merchant/i),
    ).toBeInTheDocument();
    expect(callsTo(calls, "POST")).toHaveLength(0);
  });

  it("says what an alias naming only a merchant will inherit", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    await screen.findByRole("heading", { name: "Merchants" });

    await userEvent.type(screen.getByLabelText(/raw string/i), "ZIGGO ONLINE");
    await userEvent.selectOptions(
      screen.getByLabelText(/^merchant/i, { selector: "#alias-merchant" }),
      String(FILED_MERCHANT.id),
    );

    // `POST /merchant-aliases` copies the merchant's category when none is given,
    // so the alias would arrive carrying a category nobody picked. Stated before
    // it exists rather than discovered in the list afterwards.
    expect(await screen.findByText(/inherits that category/i)).toBeInTheDocument();
  });
});

describe("merchants — deleting", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the aliases a merchant delete takes with it, before sending anything", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Albert Heijn")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /delete the merchant/i }));

    // One alias names Albert Heijn, and `merchant_alias.merchant_id` is ON DELETE
    // CASCADE — so the confirmation states the count rather than letting one
    // click remove rows the user never selected.
    expect(await screen.findByText(/1 alias named this merchant/i)).toBeInTheDocument();
    // Nothing has been sent yet: clicking Delete opens the question, it does not
    // answer it.
    expect(callsTo(calls, "DELETE")).toHaveLength(0);
  });

  it("cancels without sending anything and leaves the row alone", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Albert Heijn")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /delete the merchant/i }));
    await userEvent.click(await screen.findByRole("button", { name: /keep everything/i }));

    expect(callsTo(calls, "DELETE")).toHaveLength(0);
    expect(within(panel(MERCHANTS_PANEL)).getByText("Albert Heijn")).toBeInTheDocument();
  });

  it("sends the delete once confirmed", async () => {
    const calls = stubApi(
      baseHandlers({
        [`DELETE /merchants/${FILED_MERCHANT.id}`]: () => ({ status: 204, body: null }),
      }),
    );
    renderAt("/finance/merchants");

    const row = (await within(panel(MERCHANTS_PANEL)).findByText("Albert Heijn")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /delete the merchant/i }));
    await userEvent.click(
      await screen.findByRole("button", { name: /delete the merchant and 1 alias/i }),
    );

    await waitFor(() => {
      expect(callsTo(calls, "DELETE")).toEqual([
        { path: `/api/v1/merchants/${FILED_MERCHANT.id}`, body: null },
      ]);
    });

    // The cascaded alias goes with it, otherwise the alias panel would list an
    // alias whose merchant the screen had just reported gone.
    await waitFor(() => {
      expect(panel(ALIASES_PANEL)).not.toHaveTextContent("ALBERTHEIJN 1234 AMSTERDAM");
    });
  });

  it("leaves the row in place when the delete is refused", async () => {
    stubApi(
      baseHandlers({
        [`DELETE /merchant-aliases/${AUTO_ALIAS.id}`]: () => ({
          status: 404,
          body: { detail: "No merchant alias 8" },
        }),
      }),
    );
    renderAt("/finance/merchants");

    const row = (await within(panel(ALIASES_PANEL)).findByText("ALBERTHEIJN 1234 AMSTERDAM")).closest(
      "li",
    ) as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /delete the alias/i }));
    await userEvent.click(
      await screen.findByRole("button", { name: /^delete the alias$/i }),
    );

    // The server's own words, and the row is still on the list — a screen that
    // dropped it on a 404 would let a typo look like an edit.
    expect(await screen.findAllByText(/No merchant alias 8/)).not.toHaveLength(0);
    expect(
      within(panel(ALIASES_PANEL)).getByText("ALBERTHEIJN 1234 AMSTERDAM"),
    ).toBeInTheDocument();
  });

  it("says that deleting an alias leaves its merchant alone", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/merchants");

    const row = (await within(panel(ALIASES_PANEL)).findByText("ALBERTHEIJN 1234 AMSTERDAM")).closest(
      "li",
    ) as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /delete the alias/i }));

    // The link points one way, so the confirmation must not imply otherwise.
    expect(await screen.findByText(/the merchant it names stays/i)).toBeInTheDocument();
  });
});
