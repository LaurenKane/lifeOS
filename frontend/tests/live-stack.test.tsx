/**
 * TEMPORARY — a live-stack harness, not a committed test.
 *
 * Renders the real components against the real FastAPI app and the real
 * Postgres (docker compose `db`, migrated with `make migrate`), with `fetch`
 * untouched. Deleted after the run; the committed suite stays offline.
 *
 * Run with:
 *   VITE_API_BASE_URL=http://127.0.0.1:8000/api npx vitest run tests/live-stack.test.tsx
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Routes } from "@/routes";
import { AccountsProvider } from "@/features/finance/accounts/provider";
import { TransactionsProvider } from "@/features/finance/transactions/provider";
import { ReviewProvider } from "@/features/finance/review/provider";
import { ImportsProvider } from "@/features/finance/imports/provider";
import { BudgetsProvider } from "@/features/finance/budgets/provider";

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

const seen = (...args: unknown[]) => console.info("   >>", ...args);

/** The transaction rows, excluding the masthead's navigation. */
const rowsIn = async () => {
  const main = within(screen.getByRole("main"));
  await waitFor(() => expect(main.getAllByRole("link").length).toBeGreaterThan(0));
  return main.getAllByRole("link");
};

/** Wait for the accounts select to be populated by the real API. */
const accountsReady = async () => {
  await waitFor(() =>
    expect(screen.getByRole("option", { name: /checking/i })).toBeInTheDocument(),
  );
};

describe("live stack", () => {
  it("1. lists the accounts that are really in Postgres", async () => {
    renderAt("/finance/accounts");
    expect(await screen.findByText("Checking")).toBeInTheDocument();
    expect(screen.getByText("Expenses (system)")).toBeInTheDocument();
    seen("accounts rendered:", screen.getByRole("list").textContent);
  });

  it("2. creates an account through the form and it is really there", async () => {
    renderAt("/finance/accounts");
    await screen.findByText("Checking");

    await userEvent.type(screen.getByLabelText(/name/i), "Savings account");
    await userEvent.selectOptions(screen.getByLabelText(/type/i), "savings");
    await userEvent.selectOptions(screen.getByLabelText(/nature/i), "asset");
    await userEvent.click(screen.getByRole("button", { name: /register account/i }));

    expect(await screen.findByText(/savings account is registered as account/i)).toBeInTheDocument();

    const live = await fetch("http://127.0.0.1:8000/api/v1/accounts").then((r) => r.json());
    seen("accounts in the database now:", live.map((a: { id: number; name: string }) => `${a.id}:${a.name}`));
    expect(live.some((a: { name: string }) => a.name === "Savings account")).toBe(true);
  });

  it("3. refuses a currency the currency table does not hold, in its own words", async () => {
    renderAt("/finance/accounts");
    await screen.findByText("Checking");

    await userEvent.type(screen.getByLabelText(/name/i), "Dollars");
    await userEvent.selectOptions(screen.getByLabelText(/type/i), "cash");
    await userEvent.selectOptions(screen.getByLabelText(/nature/i), "asset");
    const currency = screen.getByLabelText(/currency/i);
    await userEvent.clear(currency);
    await userEvent.type(currency, "USD");
    await userEvent.click(screen.getByRole("button", { name: /register account/i }));

    expect(
      await screen.findByText(/unknown currency 'USD'/i),
    ).toBeInTheDocument();
    seen("the 422 the server sent, shown verbatim in the form");
  });

  it("4. lists real transactions with real minor units", async () => {
    renderAt("/finance/transactions");
    await rowsIn();
    seen("rows:", (await rowsIn()).map((r) => r.textContent));
    // −4050 minor units rendered as −40.50, with the sign carried by a glyph.
    expect((await rowsIn()).some((r) => r.textContent?.includes("−40.50"))).toBe(true);
    expect(document.body.textContent).not.toContain("-4050");
    expect(screen.getByRole("link", { name: /tamper me/i })).toBeInTheDocument();
  });

  it("5. posts a manual expense typed in the form", async () => {
    renderAt("/finance/transactions");
    await accountsReady();

    await userEvent.selectOptions(screen.getByLabelText(/account/i), "1");
    await userEvent.type(
      screen.getByLabelText(/description/i),
      `ALBERT HEIJN 1122 ${Date.now()}`,
    );
    await userEvent.type(screen.getByLabelText(/amount/i), "-12,34");
    seen("payload echo in the form:", screen.getByLabelText(/amount/i).closest("div")?.parentElement?.textContent);
    await userEvent.click(screen.getByRole("button", { name: /post to the ledger/i }));

    expect(await screen.findByText(/−12\.34 EUR is on the ledger as transaction/i)).toBeInTheDocument();
    const live = await fetch("http://127.0.0.1:8000/api/v1/transactions").then((r) => r.json());
    seen("posted:", JSON.stringify(live.at(-1)));
    expect(live.at(-1).raw_amount).toBe(-1234);
  });

  it("6. refuses the identical transaction, and says which one", async () => {
    renderAt("/finance/transactions");
    await accountsReady();

    await userEvent.selectOptions(screen.getByLabelText(/account/i), "1");
    await userEvent.type(screen.getByLabelText(/description/i), "JUMBO 4321 AMSTERDAM");
    await userEvent.type(screen.getByLabelText(/amount/i), "-40.50");
    // Same booked date as the existing row: the form defaults to TODAY, and a
    // different date is a different transaction — the fingerprint covers it, so
    // an identical entry really is identical only when all four agree.
    await userEvent.clear(screen.getByLabelText(/booked on/i));
    await userEvent.type(screen.getByLabelText(/booked on/i), "2026-10-01");
    await userEvent.click(screen.getByRole("button", { name: /post to the ledger/i }));

    expect(await screen.findByText(/an identical transaction is already recorded/i)).toBeInTheDocument();
    seen("the dedupe 409, shown in the form next to the fields that caused it");
  });

  it("7. the uncategorized queue is a real queue", async () => {
    renderAt("/finance/transactions");
    await rowsIn();
    await userEvent.click(screen.getByRole("button", { name: /needs a category/i }));

    expect(await screen.findByRole("heading", { name: /waiting on a category/i })).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(screen.getByRole("main")).getAllByRole("link").length,
      ).toBeGreaterThan(0),
    );
    const rows = within(screen.getByRole("main")).getAllByRole("link");
    seen("queue rows:", rows.map((r) => r.textContent));
    // The row that was given a category is gone from the queue; the ones that
    // were not, are here.
    expect(rows.some((r) => r.textContent?.includes("no category"))).toBe(true);
  });

  it("8. the delete refusal is the trigger's own message, and the row stays", async () => {
    renderAt("/finance/transactions/2");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    await userEvent.click(screen.getByRole("button", { name: /delete this transaction/i }));
    expect(
      await screen.findByText(/raw_data_immutable: DELETE on source_record id=2 is forbidden/),
    ).toBeInTheDocument();
    seen("refusal:", screen.getByRole("status").textContent);
    // Still here, still an amount.
    expect(screen.getByRole("heading", { name: /jumbo 4321/i })).toBeInTheDocument();
    expect(document.body.textContent).toContain("−40.50");

    const live = await fetch("http://127.0.0.1:8000/api/v1/transactions/2").then((r) => r.json());
    expect(live.id).toBe(2);
  });

  it("9. editing a category that does not exist is refused honestly", async () => {
    renderAt("/finance/transactions/2");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    await userEvent.type(screen.getByLabelText(/category/i), "99");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(await screen.findByText(/no category 99/i)).toBeInTheDocument();
    seen("the 404 from _require_category, shown next to the field");
  });

  it("10. editing a category that does exist works, and leaves the queue", async () => {
    renderAt("/finance/transactions/2");
    await screen.findByRole("heading", { name: /jumbo 4321/i });

    await userEvent.type(screen.getByLabelText(/category/i), "1");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(await screen.findByText(/saved\. this is what the ledger holds now/i)).toBeInTheDocument();

    const live = await fetch("http://127.0.0.1:8000/api/v1/transactions/2").then((r) => r.json());
    seen("after the patch:", JSON.stringify(live));
    expect(live.category_id).toBe(1);

    const queue = await fetch("http://127.0.0.1:8000/api/v1/transactions/uncategorized").then((r) => r.json());
    seen("queue after the patch:", queue.map((t: { id: number }) => t.id));
    expect(queue.some((t: { id: number }) => t.id === 2)).toBe(false);
  });

  it("11. a failed fetch shows the failure, not an empty ledger", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = ((input: string) =>
      Promise.resolve(
        input.includes("/transactions")
          ? new Response(JSON.stringify({ detail: "connection to server at db failed" }), {
              status: 503,
            })
          : original(input),
      )) as typeof fetch;
    try {
      renderAt("/finance/transactions");
      expect(await screen.findByText(/connection to server at db failed/i)).toBeInTheDocument();
      expect(screen.queryByText(/nothing recorded yet/i)).not.toBeInTheDocument();
      seen("backend down:", screen.getByRole("alert").textContent);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("12. a backend that is not there at all is named as such", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (() => Promise.reject(new TypeError("Failed to fetch"))) as typeof fetch;
    try {
      renderAt("/finance/accounts");
      expect(await screen.findByText(/could not reach the api/i)).toBeInTheDocument();
      seen("no backend:", screen.getByRole("alert").textContent);
      await waitFor(() => expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument());
    } finally {
      globalThis.fetch = original;
    }
  });
});
