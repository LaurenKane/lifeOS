/**
 * The transactions page, against a stubbed API.
 *
 * These are the behaviours that decide whether the screen can be trusted with
 * someone's money, and none of them are about styling:
 *
 *   1. A manual entry is POSTed as a SIGNED STRING. The server takes `amount`
 *      as a string so no float can enter the ledger; a form that sends a JSON
 *      number would reintroduce exactly what the contract forbids.
 *   2. A failed read shows the server's message. The old hooks did
 *      `.catch(() => setData([]))`, which renders a backend that is down as an
 *      empty ledger and then invites the user to start entering data into it.
 *   3. The delete refusal is shown, in the database's own words, and the record
 *      is still there afterwards. A control that removes a row the ledger
 *      refused to remove is a lie told with a spinner.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Routes } from "@/routes";
import { AccountsProvider } from "@/features/finance/accounts/provider";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";

const ACCOUNT = {
  id: 1,
  name: "Current account",
  currency: "EUR",
  account_type: "checking",
  account_nature: "asset",
  is_active: true,
  is_hidden: false,
  sort_order: 0,
} as const;

const TRANSACTION = {
  id: 7,
  account_id: 1,
  fingerprint: "a".repeat(64),
  raw_description: "JUMBO 4321 AMSTERDAM",
  raw_amount: -4050,
  raw_currency: "EUR",
  raw_date: "2026-10-01",
  status: "posted",
  journal_entry_id: 12,
  transfer_match_id: null,
  category_id: null,
} as const;

/** The trigger's own words, as `_as_http_error` shapes them: 409 carrying the
 * first line of the `raw_data_immutable` exception. */
const DELETE_REFUSAL = {
  detail:
    "Refused by the ledger: raw_data_immutable: DELETE on source_record id=7 is forbidden",
};

/** A fetch stub that answers per URL and method, so one test can have the list
 * succeed and the delete refuse. */
const stubApi = (handlers: Record<string, (init?: RequestInit) => { status: number; body: unknown }>) => {
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

const renderAt = (path: string) => {
  const router = createMemoryRouter(Routes, { initialEntries: [path] });
  return render(
    <AccountsProvider>
      <TransactionsProvider>
        <ReviewProvider>
          <ImportsProvider>
            <BudgetsProvider>
              <RouterProvider router={router} />
            </BudgetsProvider>
          </ImportsProvider>
        </ReviewProvider>
      </TransactionsProvider>
    </AccountsProvider>,
  );
};

const baseHandlers = () => ({
  "GET /accounts": () => ({ status: 200, body: [ACCOUNT] }),
  "GET /accounts/types": () => ({
    status: 200,
    body: ["checking", "savings", "credit_card", "cash", "investment", "loan", "mortgage"],
  }),
  "GET /accounts/natures": () => ({ status: 200, body: ["asset", "liability", "equity"] }),
  "GET /transactions": () => ({ status: 200, body: [TRANSACTION] }),
  "GET /transactions/7": () => ({ status: 200, body: TRANSACTION }),
});

describe("transactions page", () => {
  beforeEach(() => {
    // Nothing is stubbed per test until the test says so; this keeps a test that
    // forgets from silently reaching the network.
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty ledger honestly when there are no transactions at all", async () => {
    stubApi({ ...baseHandlers(), "GET /transactions": () => ({ status: 200, body: [] }) });
    renderAt("/finance/transactions");
    expect(await screen.findByText(/nothing recorded yet/i)).toBeInTheDocument();
  });

  it("shows the server's message when the list cannot be read, not an empty state", async () => {
    stubApi({
      ...baseHandlers(),
      "GET /transactions": () => ({
        status: 500,
        body: { detail: "connection to server at lifeos-db failed" },
      }),
    });
    renderAt("/finance/transactions");

    // The failure is the content. "Nothing recorded yet" would tell a user with
    // a broken database that they owe nothing.
    expect(
      await screen.findByText(/connection to server at lifeos-db failed/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/nothing recorded yet/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("renders a signed minor-unit amount with its sign, and flags an uncategorised row", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/transactions");

    const row = await screen.findByRole("link", { name: /jumbo 4321/i });
    // −4050 minor units is −40.50, not −4050.00. The amount is assembled from
    // three nodes (sign, digits, code), so it is read off the row's own text
    // rather than matched element by element.
    expect(row.textContent).toContain("−40.50");
    expect(row.textContent).not.toContain("-4050");
    expect(within(row).getByText("no category")).toBeInTheDocument();
    expect(within(row).getByText("money out")).toBeInTheDocument();
  });

  it("posts a manual entry as a signed string, and never as a JSON number", async () => {
    const calls = stubApi({
      ...baseHandlers(),
      "POST /transactions": () => ({ status: 201, body: TRANSACTION }),
    });
    renderAt("/finance/transactions");
    await screen.findByRole("link", { name: /jumbo 4321/i });

    await userEvent.selectOptions(await screen.findByLabelText(/account/i), "1");
    await userEvent.type(screen.getByLabelText(/description/i), "JUMBO 4321 AMSTERDAM");
    await userEvent.type(screen.getByLabelText(/amount/i), "-40.50");
    await userEvent.click(screen.getByRole("button", { name: /post to the ledger/i }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "POST")).toBe(true);
    });
    const post = calls.find((call) => call.method === "POST");
    const body = JSON.parse(String(post?.body)) as Record<string, unknown>;

    expect(body).toMatchObject({
      account_id: 1,
      description: "JUMBO 4321 AMSTERDAM",
      amount: "-40.50",
      currency: "EUR",
    });
    expect(typeof body.amount).toBe("string");
  });

  it("shows what a refused write said, in the ledger's words", async () => {
    stubApi({
      ...baseHandlers(),
      "GET /transactions": () => ({ status: 200, body: [] }),
      "POST /transactions": () => ({
        status: 422,
        body: {
          detail:
            "No active equity account named 'Expenses (system)'; create it before posting a manual expense.",
        },
      }),
    });
    renderAt("/finance/transactions");
    await screen.findByText(/nothing recorded yet/i);

    await userEvent.selectOptions(screen.getByLabelText(/account/i), "1");
    await userEvent.type(screen.getByLabelText(/description/i), "JUMBO 4321 AMSTERDAM");
    await userEvent.type(screen.getByLabelText(/amount/i), "-40.50");
    await userEvent.click(screen.getByRole("button", { name: /post to the ledger/i }));

    expect(
      await screen.findByText(/no active equity account named 'expenses \(system\)'/i),
    ).toBeInTheDocument();
    // Nothing claims to have been posted.
    expect(screen.queryByText(/^posted$/i)).not.toBeInTheDocument();
  });

  it("will not accept an amount with more decimals than the currency has", async () => {
    const calls = stubApi(baseHandlers());
    renderAt("/finance/transactions");
    await screen.findByRole("link", { name: /jumbo 4321/i });

    await userEvent.selectOptions(screen.getByLabelText(/account/i), "1");
    await userEvent.type(screen.getByLabelText(/amount/i), "-40.501");

    expect(
      await screen.findByText(/has 2 decimal places, not 3/i),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("tells a new user that an account comes first, instead of showing a dead form", async () => {
    stubApi({
      ...baseHandlers(),
      "GET /accounts": () => ({ status: 200, body: [] }),
      "GET /transactions": () => ({ status: 200, body: [] }),
    });
    renderAt("/finance/transactions");

    expect(await screen.findByText(/you need an account first/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /go to accounts/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /post to the ledger/i })).toBeDisabled();
  });
});

describe("transaction detail — deleting", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces the 409 refusal and leaves the record alone", async () => {
    const calls = stubApi({
      ...baseHandlers(),
      "DELETE /transactions/7": () => ({ status: 409, body: DELETE_REFUSAL }),
    });
    renderAt("/finance/transactions/7");

    await screen.findByRole("heading", { name: /jumbo 4321/i });

    // Before pressing it, the page says what is about to happen. A button that
    // only fails after the click has already lied once.
    expect(screen.getByText(/this will not remove anything/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /delete this transaction/i }));

    const refusal = await screen.findByText(/refused — nothing was deleted \(409\)/i);
    expect(refusal).toBeInTheDocument();
    expect(
      screen.getByText(/raw_data_immutable: DELETE on source_record id=7 is forbidden/),
    ).toBeInTheDocument();

    // The delete really was attempted, rather than being faked locally…
    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
    // …and nothing claims the row is gone.
    expect(screen.queryByText(/^removed$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/was removed/i)).not.toBeInTheDocument();
    // The record is still on screen, with its amount.
    expect(screen.getByRole("heading", { name: /jumbo 4321/i })).toBeInTheDocument();
    expect(document.body.textContent).toContain("−40.50");
  });

  it("only drops the row if the server says it is gone", async () => {
    // The trigger refuses every delete today. This case exists so that the code
    // keeps handling the answer it does not get today, and does not pretend the
    // refusal is the only possible one.
    stubApi({
      ...baseHandlers(),
      "DELETE /transactions/7": () => ({ status: 204, body: null }),
    });
    renderAt("/finance/transactions/7");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    await userEvent.click(screen.getByRole("button", { name: /delete this transaction/i }));
    expect(await screen.findByText(/was removed/i)).toBeInTheDocument();
  });

  it("keeps the amount out of reach of the edit form", async () => {
    stubApi(baseHandlers());
    renderAt("/finance/transactions/7");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    // Only two fields are ledger facts. An amount box that silently does
    // nothing would be worse than no amount box.
    expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/entry date/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^amount$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/a correction is a reversal/i)).toBeInTheDocument();
  });

  it("sends only the fields that were filled in", async () => {
    const calls = stubApi({
      ...baseHandlers(),
      "PATCH /transactions/7": () => ({ status: 200, body: { ...TRANSACTION, category_id: 3 } }),
    });
    renderAt("/finance/transactions/7");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    await userEvent.type(screen.getByLabelText(/category/i), "3");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === "PATCH")).toBe(true);
    });
    const patch = calls.find((call) => call.method === "PATCH");
    // entry_date is absent, and an absent field means "leave alone" to a PATCH.
    expect(JSON.parse(String(patch?.body))).toEqual({ category_id: 3 });
  });
});
