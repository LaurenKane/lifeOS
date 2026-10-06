/**
 * The categorization surface, against a stubbed API.
 *
 * The three behaviours here are the ones a wrong implementation gets wrong in
 * the direction that costs money or hides work, and none of them are about
 * styling:
 *
 *   1. RULES ARE READ AS SENTENCES, IN ENGINE ORDER. Every rule — hand and
 *      learned — is on the screen with its pattern, the category it points at
 *      and its priority. A rule hidden behind a filter is a rule that is never
 *      fixed, so there is no toggle and no pagination: the test asserts the
 *      learned rule and the hand rule are BOTH present at once.
 *
 *   2. A DELETE IS BY RULE ID, AND IT IS CONFIRMED BEFORE IT IS SENT. The
 *      endpoint removes exactly one rule, so the row it names in the
 *      confirmation is the row that goes — even where a twin carries the same
 *      text — and a refused delete leaves the rule exactly where it was. A
 *      screen that dropped a row on a 404 would let a typo look like an edit.
 *
 *   3. REMEMBERING IS OFF BY DEFAULT AND REPORTS WHAT CAME BACK. A correction
 *      must not silently teach, and the confirmation reads the server's
 *      `learned` flag rather than what the form asked for — so a response with
 *      `learned: false` is reported as "not remembered" even though the
 *      checkbox was ticked.
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
const MUSIC = { id: 31, name: "Music", kind: "expense", is_system: false } as const;
const SALARY = { id: 40, name: "Salary", kind: "income", is_system: true } as const;
const CATEGORIES = [GROCERIES, MUSIC, SALARY];

/** A hand rule at priority 100 and a learned one at 500, deliberately the other
 * way round in id order so a screen that sorted by id would fail the order
 * assertion. */
const HAND_RULE = {
  id: 9,
  description_pattern: "albert heijn",
  priority: 100,
  category_id: GROCERIES.id,
  is_learned: false,
  confidence: "1.00",
} as const;

const LEARNED_RULE = {
  id: 2,
  description_pattern: "paypal xyz",
  priority: 500,
  category_id: MUSIC.id,
  is_learned: true,
  confidence: "1.00",
} as const;

/** A second rule carrying one pattern. The delete is by id and removes exactly
 * one row, so this is what proves the twin SURVIVES — the case a delete by text
 * could not tell apart. */
const TWIN_LEARNED = {
  id: 3,
  description_pattern: "paypal xyz",
  priority: 500,
  category_id: MUSIC.id,
  is_learned: true,
  confidence: "1.00",
} as const;

/** A rule with no description pattern: the column is nullable for a rule that
 * matches on its account or merchant. There is no text to read, but there is an
 * id — so the ledger can still remove it and the button is live. */
const PATTERNLESS = {
  id: 4,
  description_pattern: null,
  priority: 10,
  category_id: SALARY.id,
  is_learned: false,
  confidence: "1.00",
} as const;

const ALL_RULES = [PATTERNLESS, HAND_RULE, LEARNED_RULE, TWIN_LEARNED];

/** A fetch stub that answers per URL and method, so one test can have the read
 * succeed and the write refuse. The same shape `review-page.test.tsx` uses;
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
  "GET /categories/kinds": () => ({ status: 200, body: ["expense", "income", "transfer", "investment"] }),
  "GET /categories/rules": () => ({ status: 200, body: ALL_RULES }),
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

/** The calls a test made, with their bodies parsed — `apiPost` sends JSON, so
 * what the server would have received is the string on the wire. */
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

/* ── Reading the rule set ──────────────────────────────────────────────────── */

describe("categorization rules — reading", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows every rule, hand and learned, together", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    expect(
      await screen.findByRole("heading", { name: "Categorization rules" }),
    ).toBeInTheDocument();

    // Both kinds of rule on one screen. A "show learned" filter would hide one
    // of these and the test would pass on the other's absence. `getAllByText`
    // because the learned pattern appears TWICE on purpose — two rules carry
    // it, which is the case the delete confirmation has to count.
    expect(await screen.findByText("albert heijn")).toBeInTheDocument();
    expect(screen.getAllByText("paypal xyz")).toHaveLength(2);

    // Read as a sentence: the pattern, then what it points at.
    const handRow = screen.getByText("albert heijn").closest("li");
    expect(handRow).not.toBeNull();
    expect(within(handRow as HTMLElement).getByText("Groceries")).toBeInTheDocument();
    expect(within(handRow as HTMLElement).getByText(/priority 100/)).toBeInTheDocument();

    const learnedRow = screen.getAllByText("paypal xyz")[0]?.closest("li");
    expect(learnedRow).not.toBeNull();
    expect(within(learnedRow as HTMLElement).getByText("Music")).toBeInTheDocument();
    expect(within(learnedRow as HTMLElement).getByText(/priority 500/)).toBeInTheDocument();
  });

  it("tells a learned rule from a hand-written one in words, not only in colour", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    const learnedRow = (await screen.findAllByText("paypal xyz"))[0]?.closest("li");
    const handRow = screen.getByText("albert heijn").closest("li");

    expect(within(learnedRow as HTMLElement).getByText("Learned")).toBeInTheDocument();
    expect(within(handRow as HTMLElement).getByText("Hand-written")).toBeInTheDocument();
  });

  it("orders the list by the engine's priority, not by id", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    await screen.findByText("albert heijn");
    const patterns = screen
      .getAllByRole("listitem")
      .map((row) => row.querySelector("p.font-mono")?.textContent)
      .filter((value): value is string => value !== null && value !== undefined);

    // The wire order is (priority, id): patternless 10, hand 100, learned 500.
    expect(patterns).toEqual(["no text pattern", "albert heijn", "paypal xyz", "paypal xyz"]);
  });

  it("reports a failed read instead of claiming there are no rules", async () => {
    stubApi(
      baseHandlers({
        "GET /categories/rules": () => ({ status: 500, body: { detail: "rules unavailable" } }),
      }),
    );
    renderAt("/finance/categories/rules");

    // The empty state would invite the user to write a rule that already exists.
    expect(await screen.findByText("rules unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/no rules yet/i)).not.toBeInTheDocument();
  });

  it("still offers delete for a rule with no text pattern", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    const row = (await screen.findByText("no text pattern")).closest("li");
    expect(row).not.toBeNull();
    /* The delete is by id, so a rule with nothing to read is still addressable.
       A screen that refused it would be refusing something the ledger does. */
    expect(
      within(row as HTMLElement).getByRole("button", { name: /delete rule 4/i }),
    ).toBeEnabled();
    /* And it says what it matches on rather than apologising for a limit that
       no longer exists. */
    expect(within(row as HTMLElement).getByText(/matches on its account or merchant/i)).toBeInTheDocument();
  });
});

/* ── Writing a rule ────────────────────────────────────────────────────────── */

describe("categorization rules — writing", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a hand rule at priority 100 and sends the substring", async () => {
    let created: { description_pattern: string; category_id: number; priority: number } | null = null;
    const calls = stubApi(
      baseHandlers({
        "POST /categories/rules": (init) => {
          created = JSON.parse(String(init?.body));
          return {
            status: 201,
            body: { id: 99, ...created, is_learned: false, confidence: "1.00" },
          };
        },
      }),
    );
    renderAt("/finance/categories/rules");

    await userEvent.type(
      await screen.findByLabelText(/description pattern/i),
      "  albert heijn  ",
    );
    await userEvent.selectOptions(screen.getByLabelText(/^category$/i), String(MUSIC.id));
    await userEvent.click(screen.getByRole("button", { name: /add this rule/i }));

    await waitFor(() => {
      expect(created).not.toBeNull();
    });
    /* Trimmed, and at the hand-rule tier — which is the whole reason a
       hand-written rule outranks everything the system learned. */
    expect(created).toEqual({
      description_pattern: "albert heijn",
      category_id: MUSIC.id,
      priority: 100,
    });
    expect(callsTo(calls, "POST").some((call) => call.path.includes("/categories/rules"))).toBe(
      true,
    );

    // And it says what it added.
    expect(await screen.findByText(/now points at Music/i)).toBeInTheDocument();
  });

  it("refuses to send a rule with no category, and explains why", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    const input = await screen.findByLabelText(/description pattern/i);
    await userEvent.type(input, "albert heijn");

    expect(screen.getByRole("button", { name: /add this rule/i })).toBeDisabled();
    expect(callsTo(calls, "POST")).toHaveLength(0);
  });

  it("reports the server's own words when the rule is refused", async () => {
    stubApi(
      baseHandlers({
        "POST /categories/rules": () => ({
          status: 409,
          body: { detail: "A rule already matches that pattern" },
        }),
      }),
    );
    renderAt("/finance/categories/rules");

    await userEvent.type(
      await screen.findByLabelText(/description pattern/i),
      "albert heijn",
    );
    await userEvent.selectOptions(screen.getByLabelText(/^category$/i), String(MUSIC.id));
    await userEvent.click(screen.getByRole("button", { name: /add this rule/i }));

    expect(
      await screen.findByText("A rule already matches that pattern"),
    ).toBeInTheDocument();
  });
});

/* ── Deleting a rule ───────────────────────────────────────────────────────── */

describe("categorization rules — deleting", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("confirms before deleting, names the rule by id, and removes only that one", async () => {
    const calls = stubApi(
      baseHandlers({
        /* Keyed by the RULE ID, which is the whole segment: the endpoint takes
           an integer, so there is nothing to percent-encode and no way for a
           pattern's characters to address a different route. */
        "DELETE /categories/rules/2": () => ({
          status: 200,
          body: { status: "deleted", id: 2 },
        }),
      }),
    );
    renderAt("/finance/categories/rules");

    /* Both twins carry the same text, so a name built from the pattern alone
       would read identically twice. The id is in the accessible name instead,
       which is also the number the delete sends. */
    const twins = await screen.findAllByRole("button", {
      name: /delete rule (2|3), matching paypal xyz/i,
    });
    expect(twins).toHaveLength(2);
    await userEvent.click(twins[0] as HTMLElement);

    /* Nothing sent yet, and both twins are still on screen. */
    expect(callsTo(calls, "DELETE")).toHaveLength(0);
    expect(screen.getAllByText("paypal xyz")).toHaveLength(2);

    await userEvent.click(screen.getByRole("button", { name: /^delete this rule$/i }));

    await waitFor(() => {
      expect(callsTo(calls, "DELETE")).toHaveLength(1);
    });
    // Only the rule that was asked for. This is the assertion the whole change
    // turns on: a delete by pattern would take both rows.
    await waitFor(() => {
      expect(screen.queryAllByText("paypal xyz")).toHaveLength(1);
    });
    expect(callsTo(calls, "DELETE")[0]?.path).toBe("/api/v1/categories/rules/2");
    /* And the screen says the twin is still there rather than leaving the user
       to wonder whether the other one went too. */
    expect(
      screen.getByText(/one other rule carries the same text and is still here/i),
    ).toBeInTheDocument();
  });

  it("keeps the rules when the delete is cancelled", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/categories/rules");

    /* The ROW button, which is named after the pattern it acts on — a screen
       reader user hears which rule they are about to delete before pressing it. */
    await userEvent.click(
      await screen.findByRole("button", { name: /delete rule 9/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^keep this rule$/i }));

    expect(callsTo(calls, "DELETE")).toHaveLength(0);
    expect(screen.getByText("albert heijn")).toBeInTheDocument();
    /* The confirmation is gone, so the screen is back to exactly what it was. */
    expect(
      screen.queryByRole("button", { name: /^keep this rule$/i }),
    ).not.toBeInTheDocument();
  });

  it("leaves the rule in place and shows the reason when the delete is refused", async () => {
    stubApi(
      baseHandlers({
        "DELETE /categories/rules/9": () => ({
          status: 404,
          body: { detail: "No rule 9" },
        }),
      }),
    );
    renderAt("/finance/categories/rules");

    await userEvent.click(
      await screen.findByRole("button", { name: /delete rule 9/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete this rule$/i }));

    expect(await screen.findByText("No rule 9")).toBeInTheDocument();
    // A screen that dropped the row on a 404 would let a typo look like an edit.
    expect(screen.getByText("albert heijn")).toBeInTheDocument();
  });

  it("addresses a pattern holding a slash by its rule id", async () => {
    /* The case the id-based endpoint exists for. Under `DELETE
       /categories/rules/{pattern}` a pattern of `bakker/straat` was unaddressable
       however it was encoded — the path parameter is one segment, and `%2F`
       inside a segment is a character, not a separator. So every encoding 404'd
       for a rule `GET /categories/rules` plainly listed, and the row had to carry
       an apology instead of a button. */
    const calls = stubApi(
      baseHandlers({
        "GET /categories/rules": () => ({
          status: 200,
          body: [{ ...HAND_RULE, description_pattern: "bakker/straat" }],
        }),
        "DELETE /categories/rules/9": () => ({
          status: 200,
          body: { status: "deleted", id: 9 },
        }),
      }),
    );
    renderAt("/finance/categories/rules");

    const row = (await screen.findByText("bakker/straat")).closest("li");
    expect(row).not.toBeNull();
    const button = within(row as HTMLElement).getByRole("button", { name: /delete rule 9/i });
    expect(button).toBeEnabled();
    /* And no excuse on the row any more. */
    expect(within(row as HTMLElement).queryByText(/holds a slash/i)).toBeNull();

    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: /^delete this rule$/i }));

    await waitFor(() => {
      /* The slash is nowhere in the request line. It is the number, and nothing
         is encoded because there is nothing that could need it. */
      expect(callsTo(calls, "DELETE")[0]?.path).toBe("/api/v1/categories/rules/9");
    });
    await waitFor(() => {
      expect(screen.queryByText("bakker/straat")).toBeNull();
    });
  });
});