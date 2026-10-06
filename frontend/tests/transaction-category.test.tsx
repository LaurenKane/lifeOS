/**
 * Transaction detail: the category picker, and the learn affordance beside it.
 *
 * This is where the review queue actually drains, so the three behaviours here
 * are the three ways getting it wrong costs a user money:
 *
 *   1. THE CATEGORY IS A NAME, NOT AN ID. The field used to be a monospace box
 *      holding a raw `category_id`. Nobody standing at a transaction knows that
 *      12 is "Groceries", and a picker that offers names grouped by kind is the
 *      only version of this a person can use.
 *
 *   2. REMEMBERING IS OFF BY DEFAULT. The test types a correction, saves, and
 *      asserts the request carried NO `learn` key. A correction that silently
 *      teaches writes a rule at priority 500 that keeps firing after the screen
 *      is closed, and the user never saw it.
 *
 *   3. THE CONFIRMATION REPORTS WHAT CAME BACK, NOT WHAT WAS ASKED FOR. Ticking
 *      the box and getting `learned: false` back is a real outcome — the ledger
 *      accepted the category and stored no rule — and the screen must say that
 *      rather than claim the payee was remembered.
 */
import { render, screen, waitFor } from "@testing-library/react";
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

/** An uncategorised money-out line: the shape the review queue hands over. */
const QUEUED = {
  id: 501,
  account_id: 1,
  fingerprint: "a4334bdd8e519b410838e9a6ba8e64fcec946a58c3488c53e4b7a6d1dd620f2f",
  raw_description: "PAYPAL XYZ 1234",
  raw_amount: -1250,
  raw_currency: "EUR",
  raw_date: "2026-10-01",
  status: "posted",
  journal_entry_id: 9,
  transfer_match_id: null,
  category_id: null,
  learned: false,
} as const;

/** The same line once filed. `learned` is what the PATCH response carries back,
 * and it is the only thing that says whether a lesson was stored. */
const filed = (categoryId: number, learned: boolean) => ({ ...QUEUED, category_id: categoryId, learned });

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
  "GET /transactions": () => ({ status: 200, body: [QUEUED] }),
  "GET /transactions/501": () => ({ status: 200, body: QUEUED }),
  "GET /review/transfers": () => ({ status: 200, body: [] }),
  "GET /review/transfers/stats": () => ({ status: 200, body: { multi_candidate: 0, low_confidence: 0, total: 0 } }),
  ...overrides,
});

const renderDetail = () => {
  const router = createMemoryRouter(Routes, {
    initialEntries: ["/finance/transactions/501"],
  });
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

const patchBodies = (
  calls: Array<{ method: string; path: string; body: unknown }>,
): Array<Record<string, unknown>> =>
  calls
    .filter((call) => call.method === "PATCH")
    .map((call) =>
      typeof call.body === "string"
        ? (JSON.parse(call.body) as Record<string, unknown>)
        : ({} as Record<string, unknown>),
    );

describe("transaction detail — the category picker", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers category names grouped by kind, not raw ids", async () => {
    stubApi(baseHandlers());
    renderDetail();

    const picker = (await screen.findByLabelText(/^category$/i)) as HTMLSelectElement;
    expect(picker.tagName).toBe("SELECT");

    /* Grouped, because `kind` decides a balance's sign and a flat list of names
       hides it. Queried as `optgroup` elements rather than by ARIA role: the
       group role is what a `<select>` exposes, and the elements are what the
       grouping actually is — asserting the element avoids a jsdom role-mapping
       quirk turning a real grouping into a test failure. */
    expect(
      Array.from(picker.querySelectorAll("optgroup")).map((group) => group.label),
    ).toEqual(["Expense", "Income"]);
    expect(
      Array.from(picker.options).map((option) => option.textContent),
    ).toEqual(expect.arrayContaining(["Groceries · 12 · system", "Music · 31"]));
  });

  it("files the transaction under the chosen category's id", async () => {
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: filed(MUSIC.id, false) }),
      }),
    );
    renderDetail();

    await userEvent.selectOptions(
      await screen.findByLabelText(/^category$/i),
      String(MUSIC.id),
    );
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(1);
    });
    expect(patchBodies(calls)[0]).toEqual({ category_id: MUSIC.id });
    expect(await screen.findByText(/only this one changed/i)).toBeInTheDocument();
  });

  it("sends nothing at all when nothing is changed", async () => {
    const calls = stubApi(baseHandlers());
    renderDetail();

    await screen.findByLabelText(/^category$/i);
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    expect(patchBodies(calls)).toHaveLength(0);
    expect(await screen.findByText(/nothing to change/i)).toBeInTheDocument();
  });
});

describe("transaction detail — remembering the payee", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is off by default, and a plain correction teaches nothing", async () => {
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: filed(MUSIC.id, false) }),
      }),
    );
    renderDetail();

    const checkbox = (await screen.findByLabelText(/remember this payee/i)) as HTMLInputElement;
    expect(checkbox.type).toBe("checkbox");
    expect(checkbox.checked).toBe(false);

    await userEvent.selectOptions(screen.getByLabelText(/^category$/i), String(MUSIC.id));
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(1);
    });
    /* No `learn` key at all — not `learn: false`. A correction must not silently
       teach, and the strongest form of that is the flag never being sent. */
    expect(patchBodies(calls)[0]).not.toHaveProperty("learn");
  });

  it("cannot be ticked before a category is chosen", async () => {
    stubApi(baseHandlers());
    renderDetail();

    const checkbox = (await screen.findByLabelText(/remember this payee/i)) as HTMLInputElement;
    expect(checkbox).toBeDisabled();
    expect(screen.getByText(/nothing to remember until the transaction says what it is/i)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText(/^category$/i), String(MUSIC.id));
    expect(checkbox).toBeEnabled();
  });

  it("sends learn:true when ticked, and says what was stored", async () => {
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: filed(MUSIC.id, true) }),
      }),
    );
    renderDetail();

    await userEvent.selectOptions(
      await screen.findByLabelText(/^category$/i),
      String(MUSIC.id),
    );
    await userEvent.click(screen.getByLabelText(/remember this payee/i));
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(1);
    });
    expect(patchBodies(calls)[0]).toEqual({ category_id: MUSIC.id, learn: true });

    /* The confirmation names the category and links to where the rule is readable.
       It deliberately does NOT print a pattern: the stored one is
       `stable_payee_pattern` server-side (trailing numeric tokens dropped, at
       most four words, lowercased), and a frontend copy of that rule would print
       text that is not what was stored the day the server changed it. */
    expect(await screen.findByText(/filed, and remembered/i)).toBeInTheDocument();
    expect(screen.getByText(/stable/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /see the rule/i })).toHaveAttribute(
      "href",
      "/finance/categories/rules",
    );
  });

  it("reports a refusal to remember when the server stored no rule", async () => {
    /* Ticked, category sent, and `learned` came back false: the category stands
       and no lesson was taught. Saying "saved" would be claiming a correction
       taught something it did not. */
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: filed(MUSIC.id, false) }),
      }),
    );
    renderDetail();

    await userEvent.selectOptions(
      await screen.findByLabelText(/^category$/i),
      String(MUSIC.id),
    );
    await userEvent.click(screen.getByLabelText(/remember this payee/i));
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(1);
    });
    expect(patchBodies(calls)[0]).toEqual({ category_id: MUSIC.id, learn: true });

    expect(await screen.findByText(/filed — but not remembered/i)).toBeInTheDocument();
    expect(screen.getByText(/stored no rule for this description/i)).toBeInTheDocument();
    expect(screen.getByText(/the next one from this payee will ask again/i)).toBeInTheDocument();
  });

  it("resets the box after saving, so the next correction does not teach too", async () => {
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: filed(MUSIC.id, true) }),
      }),
    );
    renderDetail();

    const picker = await screen.findByLabelText(/^category$/i);
    const checkbox = screen.getByLabelText(/remember this payee/i) as HTMLInputElement;

    await userEvent.selectOptions(picker, String(MUSIC.id));
    await userEvent.click(checkbox);
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(checkbox.checked).toBe(false);
    });

    // A second correction without ticking teaches nothing.
    await userEvent.selectOptions(picker, String(GROCERIES.id));
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(2);
    });
    expect(patchBodies(calls)[1]).toEqual({ category_id: GROCERIES.id });
  });

  it("does not send learn for a date-only correction", async () => {
    const calls = stubApi(
      baseHandlers({
        "PATCH /transactions/501": () => ({ status: 200, body: QUEUED }),
      }),
    );
    renderDetail();

    await screen.findByLabelText(/^category$/i);
    await userEvent.type(screen.getByLabelText(/entry date/i), "2026-10-05");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(patchBodies(calls)).toHaveLength(1);
    });
    expect(patchBodies(calls)[0]).toEqual({ entry_date: "2026-10-05" });
  });
});

