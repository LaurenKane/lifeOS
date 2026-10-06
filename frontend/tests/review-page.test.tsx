/**
 * The transfer review queue, against a stubbed API.
 *
 * These are the behaviours that decide whether the screen can be trusted with
 * someone's money, and none of them are about styling:
 *
 *   1. Both legs of a pair are legible enough to judge: amount, account, date,
 *      for the outbound line and for every candidate, and an amount that is
 *      the exact negation of the outbound is stated rather than left to be
 *      worked out in one's head.
 *   2. More than one candidate means a choice, not a guess. CONFIRM stays
 *      closed until a candidate is named, and the body carries the chosen
 *      `journal_line_id` — never the first one by default.
 *   3. A refused decision puts the item BACK, in the queue, with the server's
 *      own words. A queue that drops a row on a failed write is a queue that
 *      loses work: the click vanished and the ledger never heard about it.
 *   4. A failed READ is never an empty queue. "Nothing in the queue" told to a
 *      user with a dead backend is a statement about their money.
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

/** The outbound leg: fifty euros leaving the current account to a card. */
const OUTBOUND = {
  journal_line_id: 880,
  description: "AMERICAN EXPRESS",
  amount_minor: -5000,
  currency: "EUR",
  booked_date: "2026-09-28",
  account_id: 1,
  account_name: "Rabobank Current",
} as const;

/** The candidate the matcher wants: the same money arriving in the savings
 * account, two days later. */
const CANDIDATE = {
  journal_line_id: 911,
  description: "SEPA OVERBOEKING",
  amount_minor: 5000,
  currency: "EUR",
  booked_date: "2026-09-30",
  account_id: 2,
  account_name: "Revolut Savings",
  confidence: "high",
} as const;

/** The one that is not it: the same amount, but money arriving in the account
 * the money just left, which no own-account transfer can be. */
const SAME_ACCOUNT_CANDIDATE = {
  journal_line_id: 904,
  description: "TERUGGAVE PINAUTOMAT",
  amount_minor: 5000,
  currency: "EUR",
  booked_date: "2026-09-28",
  account_id: 1,
  account_name: "Rabobank Current",
  confidence: "low",
} as const;

/** One candidate, one reason: the plain case. */
const ITEM = {
  id: 41,
  outbound: OUTBOUND,
  candidates: [CANDIDATE],
  reason: "low_confidence",
  created_at: "2026-10-01T09:12:04",
} as const;

/** Two candidates, the other reason: the pair has to be chosen. */
const AMBIGUOUS_ITEM = {
  id: 42,
  outbound: OUTBOUND,
  candidates: [SAME_ACCOUNT_CANDIDATE, CANDIDATE],
  reason: "multi_candidate",
  created_at: "2026-10-01T09:14:22",
} as const;

const STATS = { multi_candidate: 1, low_confidence: 2, total: 3 };

/** A fetch stub that answers per URL and method, so one test can have the read
 * succeed and the write refuse. The same shape `transactions-page.test.tsx`
 * uses; unhandled paths answer with an empty collection. */
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

const renderReview = () => {
  const router = createMemoryRouter(Routes, { initialEntries: ["/finance/review"] });
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

/** A queue that answers with `items` until something changes it. A decided item
 * really does leave the server's list, so the stub has to let it — otherwise
 * the re-read after every decision would put the row straight back and no
 * assertion about removal would mean anything. */
const queue = (items: ReadonlyArray<unknown>) => {
  let resolved = false;
  return {
    markResolved: () => {
      resolved = true;
    },
    handlers: {
      "GET /review/transfers": () => ({ status: 200, body: resolved ? [] : items }),
      "GET /review/transfers/stats": () => ({ status: 200, body: STATS }),
    },
  };
};

/** The one write a test made, with its body left as it was sent — `reject` and
 * `ignore` send no body at all, and that has to be assertable. */
const postCall = (
  calls: Array<{ method: string; path: string; body: unknown }>,
): { path: string; raw: unknown; body: Record<string, unknown> | null } => {
  const call = calls.find((entry) => entry.method === "POST");
  if (call === undefined) {
    throw new Error("no POST was sent");
  }
  return {
    path: call.path,
    raw: call.body,
    body:
      typeof call.body === "string" ? (JSON.parse(call.body) as Record<string, unknown>) : null,
  };
};

describe("transfer review queue — reading", () => {
  beforeEach(() => {
    // Nothing is stubbed per test until the test says so; this keeps a test that
    // forgets from silently reaching the network.
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty queue honestly when there is nothing to decide", async () => {
    stubApi(queue([]).handlers);
    renderReview();
    expect(await screen.findByText(/nothing in the queue/i)).toBeInTheDocument();
  });

  it("shows the server's message when the queue cannot be read, not an empty state", async () => {
    stubApi({
      ...queue([ITEM]).handlers,
      "GET /review/transfers": () => ({
        status: 500,
        body: { detail: "transfer_match is not available" },
      }),
    });
    renderReview();

    // The failure is the content. "Nothing in the queue" would tell a user with
    // a broken backend that they owe nothing.
    expect(await screen.findByText(/transfer_match is not available/)).toBeInTheDocument();
    expect(screen.queryByText(/nothing in the queue/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("reads the queue over the v1 path, and the counts separately", async () => {
    const calls = stubApi(queue([ITEM]).handlers);
    renderReview();
    await screen.findByText("AMERICAN EXPRESS");

    const paths = calls.map((call) => call.path);
    expect(paths).toContain("/api/v1/review/transfers");
    expect(paths).toContain("/api/v1/review/transfers/stats");
    // The provider owns the fetch; the page must not re-request the same queue.
    expect(paths.filter((path) => path === "/api/v1/review/transfers")).toHaveLength(1);
  });

  it("shows how deep the queue is and why, from the counts endpoint", async () => {
    stubApi(queue([ITEM]).handlers);
    renderReview();

    /* Matched on the innermost element: the sentence is nested inside the
     * panel's own description paragraph, and both carry the same text. */
    const depth = await screen.findByText(
      (_content, element) =>
        element?.tagName === "SPAN" && /waiting/.test(element.textContent ?? ""),
    );
    expect(depth).toHaveTextContent("3 waiting");
    expect(depth).toHaveTextContent("1 with more than one candidate");
    expect(depth).toHaveTextContent("2 below the confidence threshold");
  });

  it("keeps a failing count from hiding the queue, and says the count failed", async () => {
    stubApi({
      ...queue([ITEM]).handlers,
      "GET /review/transfers/stats": () => ({ status: 500, body: { detail: "stats boom" } }),
    });
    renderReview();

    expect(await screen.findByText(/stats boom/)).toBeInTheDocument();
    // The queue itself is readable, so it is still on screen.
    expect(screen.getByText("AMERICAN EXPRESS")).toBeInTheDocument();
  });
});

describe("transfer review queue — judging a pair", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows both legs with the amount, account, date and confidence of each", async () => {
    stubApi(queue([AMBIGUOUS_ITEM]).handlers);
    renderReview();

    // Both descriptions, both accounts, both dates: the judgement is a
    // comparison, and everything it compares has to be on the page.
    expect(await screen.findByText("AMERICAN EXPRESS")).toBeInTheDocument();
    expect(screen.getByText("SEPA OVERBOEKING")).toBeInTheDocument();
    expect(screen.getByText("TERUGGAVE PINAUTOMAT")).toBeInTheDocument();
    // Rabobank Current appears twice — the outbound leg's account, and the
    // candidate that claims to arrive in the same one.
    expect(screen.getAllByText("Rabobank Current")).toHaveLength(2);
    expect(screen.getByText("Revolut Savings")).toBeInTheDocument();
    expect(screen.getAllByText("2026-09-28").length).toBeGreaterThan(0);
    expect(screen.getByText("2026-09-30")).toBeInTheDocument();

    // The matcher's own score, and both amounts with their signs. −50.00 above
    // +50.00 is the pair, read at a glance off one aligned column.
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("low")).toBeInTheDocument();
    expect(document.body.textContent).toContain("−50.00");
    expect(document.body.textContent).toContain("+50.00");
    // Never the raw minor units: −5000 is fifty euros, not five thousand.
    expect(document.body.textContent).not.toContain("-5000");
  });

  it("states the two facts that decide a pair, instead of leaving them to be worked out", async () => {
    stubApi(queue([AMBIGUOUS_ITEM]).handlers);
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    // The exact negation in the same currency is the strongest evidence there
    // is, and the one the eye has to do arithmetic to find.
    expect(screen.getAllByText("same amount").length).toBe(2);
    // Money cannot leave and re-enter one own account, so the candidate on the
    // same account is flagged rather than merely shown.
    expect(screen.getAllByText("same account")).toHaveLength(1);
  });

  it("says how many days apart two legs were booked", async () => {
    stubApi(queue([ITEM]).handlers);
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");
    expect(screen.getByText(/2 days apart/)).toBeInTheDocument();
  });

  it("shows the reason an item is in the queue, and which item it is", async () => {
    stubApi(queue([ITEM]).handlers);
    renderReview();
    expect(await screen.findByText("below the confidence threshold")).toBeInTheDocument();
    // The id is evidence a person may have to quote back, so it is data.
    expect(screen.getByText(/item 41/)).toBeInTheDocument();
  });
});

describe("transfer review queue — deciding", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("confirms the only candidate without asking the user to choose", async () => {
    const pending = queue([ITEM]);
    const calls = stubApi({
      ...pending.handlers,
      "POST /review/transfers/41/confirm": () => {
        pending.markResolved();
        return { status: 200, body: {} };
      },
    });
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    // One candidate is a yes/no, so there is no radio to press first.
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^confirm review item 41$/i }));

    await waitFor(() =>
      expect(postCall(calls).path).toBe("/api/v1/review/transfers/41/confirm"),
    );
    // The candidate being confirmed is named, not left null for the server to
    // guess between.
    expect(postCall(calls).body).toEqual({ candidate_journal_line_id: 911 });
    // Optimistic removal: the item leaves once the click is answered.
    await waitFor(() =>
      expect(screen.queryByText("SEPA OVERBOEKING")).not.toBeInTheDocument(),
    );
  });

  it("will not confirm an ambiguous item until a candidate is chosen", async () => {
    const calls = stubApi(queue([AMBIGUOUS_ITEM]).handlers);
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    // Two candidates is a question, not a yes/no: the button stays closed and
    // says why, rather than defaulting to the first row.
    expect(screen.getByRole("button", { name: /^confirm review item 42$/i })).toBeDisabled();
    expect(
      screen.getByText(/choose the candidate that is the other half/i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: /sepa overboeking/i }));
    expect(screen.getByRole("button", { name: /^confirm review item 42$/i })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /^confirm review item 42$/i }));

    await waitFor(() =>
      expect(postCall(calls).path).toBe("/api/v1/review/transfers/42/confirm"),
    );
    // The chosen candidate, which is NOT the first one listed.
    expect(postCall(calls).body).toEqual({ candidate_journal_line_id: 911 });
  });

  it("rejects as not a transfer, with no body", async () => {
    const pending = queue([ITEM]);
    const calls = stubApi({
      ...pending.handlers,
      "POST /review/transfers/41/reject": () => {
        pending.markResolved();
        return { status: 200, body: {} };
      },
    });
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    await userEvent.click(screen.getByRole("button", { name: /reject review item 41/i }));
    await waitFor(() => expect(postCall(calls).path).toBe("/api/v1/review/transfers/41/reject"));
    // Reject takes no body: the endpoint takes none, and an invented empty
    // object would be a request the contract never described.
    expect(postCall(calls).raw).toBeUndefined();
    await waitFor(() =>
      expect(screen.queryByText("SEPA OVERBOEKING")).not.toBeInTheDocument(),
    );
  });

  it("ignores an item, with no body", async () => {
    const pending = queue([ITEM]);
    const calls = stubApi({
      ...pending.handlers,
      "POST /review/transfers/41/ignore": () => {
        pending.markResolved();
        return { status: 200, body: {} };
      },
    });
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    await userEvent.click(screen.getByRole("button", { name: /ignore review item 41/i }));
    await waitFor(() => expect(postCall(calls).path).toBe("/api/v1/review/transfers/41/ignore"));
    expect(postCall(calls).raw).toBeUndefined();
    await waitFor(() =>
      expect(screen.queryByText("SEPA OVERBOEKING")).not.toBeInTheDocument(),
    );
  });

  it("confirms an item with no candidate by naming none", async () => {
    const candidateLess = { ...ITEM, candidates: [] };
    const calls = stubApi({
      ...queue([candidateLess]).handlers,
      "POST /review/transfers/41/confirm": () => ({ status: 200, body: {} }),
    });
    renderReview();
    await screen.findByText("AMERICAN EXPRESS");

    // An item the matcher found nothing for is a real case, and confirming it
    // clears the item without inventing a counterpart.
    expect(screen.getByText(/no candidate was found/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^confirm review item 41$/i }));
    await waitFor(() => expect(postCall(calls).body).toEqual({ candidate_journal_line_id: null }));
  });

  it("puts the item back and shows the refusal when the write fails", async () => {
    const calls = stubApi({
      ...queue([ITEM]).handlers,
      "POST /review/transfers/41/confirm": () => ({
        status: 409,
        body: { detail: "transfer_match already exists for journal_line_id 880" },
      }),
    });
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    await userEvent.click(screen.getByRole("button", { name: /^confirm review item 41$/i }));

    // The server's own words, and the status it refused with.
    expect(await screen.findByText(/confirm was not saved/i)).toBeInTheDocument();
    expect(
      screen.getAllByText(/transfer_match already exists for journal_line_id 880/).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/item 41 is back in the queue/i)).toBeInTheDocument();

    // The work is still there — the item, with its amount, at the same place in
    // the queue. The click did not vanish; it failed.
    await waitFor(() => expect(screen.getByText("SEPA OVERBOEKING")).toBeInTheDocument());
    expect(document.body.textContent).toContain("−50.00");

    // …and the queue was re-read from the server rather than patched locally,
    // because the server's list is the truth about what is still queued.
    await waitFor(() =>
      expect(
        calls.filter(
          (call) => call.method === "GET" && call.path === "/api/v1/review/transfers",
        ).length,
      ).toBeGreaterThan(1),
    );
  });

  it("announces the outcome, because the row it belonged to is gone", async () => {
    const pending = queue([ITEM]);
    stubApi({
      ...pending.handlers,
      "POST /review/transfers/41/ignore": () => {
        pending.markResolved();
        return { status: 200, body: {} };
      },
    });
    renderReview();
    await screen.findByText("SEPA OVERBOEKING");

    await userEvent.click(screen.getByRole("button", { name: /ignore review item 41/i }));
    expect(await screen.findByText(/item 41 ignored/i)).toBeInTheDocument();
  });

  it("keeps one item's decisions out of another item's way", async () => {
    stubApi(queue([ITEM, AMBIGUOUS_ITEM]).handlers);
    renderReview();
    // SEPA OVERBOEKING is a candidate on both items, so it appears twice.
    await screen.findAllByText("SEPA OVERBOEKING");

    // Two items, two sets of decisions. The accessible names carry the item id,
    // so a screen reader announces "Confirm review item 41" rather than two
    // identical "Confirm" buttons.
    const confirms = screen.getAllByRole("button", { name: /confirm review item/i });
    expect(confirms).toHaveLength(2);
    expect(confirms[0]).toHaveAccessibleName("Confirm review item 41");
    expect(confirms[1]).toHaveAccessibleName("Confirm review item 42");

    // An ambiguous item's confirm is still closed; the plain item is unaffected.
    expect(confirms[0]).toBeEnabled();
    expect(confirms[1]).toBeDisabled();

    // The radio's accessible name is the candidate row's own words — the
    // description, account, date and amount a person is judging by.
    const radio = screen.getByRole("radio", { name: /teruggave pinautomat/i });
    expect(radio).toHaveAccessibleName(/TERUGGAVE PINAUTOMAT/);
    expect(radio).toHaveAccessibleName(/Rabobank Current/);
    expect(radio).toHaveAccessibleName(/2026-09-28/);
    expect(radio).toHaveAccessibleName(/\+50\.00/);
    const row = radio.closest("label");
    expect(within(row as HTMLElement).getByText("Candidate 1")).toBeInTheDocument();
  });
});
