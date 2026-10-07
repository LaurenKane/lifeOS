/**
 * The Overview page, against a stubbed API.
 *
 * Almost none of this is about styling. These are the claims the page makes about
 * a person's money, and each one is a way a finance tool can be plausible and
 * wrong:
 *
 *   1. An EMPTY LEDGER SHOWS NO FIGURE. `0.00` on an account that was never
 *      opened is a claim — it says the user is worth nothing rather than that
 *      nothing has been recorded.
 *   2. DEMO FIGURES ARE NEVER SUBSTITUTED FOR REAL ONES. There is no code path
 *      from a failed or empty read to the demo, and the demo says so on screen in
 *      three places while it is up.
 *   3. A FAILED READ IS LOUD. Not an empty state, not a zero.
 *   4. THE REVIEW STUB NEVER CLAIMS A CLEAR QUEUE IT DID NOT LOOK AT.
 *   5. FIGURES ARE INTEGER MINOR UNITS AND ALWAYS SET IN THE MONOSPACE, in
 *      either prose face, because a column of money has to align.
 *   6. NO KICKER ABOVE ANY HEADING.
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
  name: "Betaalrekening",
  currency: "EUR",
  account_type: "checking",
  account_nature: "asset",
  is_active: true,
  is_hidden: false,
  sort_order: 0,
} as const;

const TRANSACTION = (over: Partial<typeof TRANSACTION_BASE> = {}) => ({
  ...TRANSACTION_BASE,
  ...over,
});

const TRANSACTION_BASE = {
  id: 1,
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

type Handler = () => { status: number; body: unknown };

/** A fetch stub that answers per method and path. */
const stubApi = (handlers: Record<string, Handler>) => {
  const fetchMock = vi.fn((input: unknown, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const key = `${method} ${path.replace("/api/v1", "").replace("/api", "").split("?")[0] ?? ""}`;
    const handler = handlers[key];
    if (handler === undefined) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    const { status, body } = handler();
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const EMPTY_HANDLERS = (): Record<string, Handler> => ({
  "GET /accounts": () => ({ status: 200, body: [] }),
  "GET /accounts/types": () => ({ status: 200, body: ["checking", "savings"] }),
  "GET /accounts/natures": () => ({ status: 200, body: ["asset", "liability", "equity"] }),
  "GET /transactions": () => ({ status: 200, body: [] }),
});

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

/** A populated ledger, for the cases that need a figure on screen. */
const POPULATED = (): Record<string, Handler> => ({
  ...EMPTY_HANDLERS(),
  "GET /accounts": () => ({ status: 200, body: [ACCOUNT] }),
  "GET /transactions": () => ({ status: 200, body: [TRANSACTION()] }),
  "GET /analytics/net-worth": () => ({
    status: 200,
    body: [
      { date: "2026-09-01", net_worth: 4_000_000 },
      { date: "2026-09-20", net_worth: 4_100_000 },
      { date: "2026-10-05", net_worth: 4_250_000 },
    ],
  }),
  "GET /analytics/spend-by-category": () => ({
    status: 200,
    body: [
      { category_id: 1, category_name: "Huur", kind: "expense", amount: -118_900 },
      { category_id: 2, category_name: "Apotheek", kind: "expense", amount: -1_237 },
    ],
  }),
  "GET /analytics/cashflow": () => ({
    status: 200,
    body: [
      { period: "2026-09", income: 331_458, expense: 361_889, net: -30_431 },
      { period: "2026-10", income: 348_542, expense: 128_800, net: 219_742 },
    ],
  }),
});

describe("overview — an empty ledger", () => {
  beforeEach(() => {
    stubApi(EMPTY_HANDLERS());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("shows no figure at all, and says what is missing", async () => {
    renderAt("/finance/overview");
    expect(await screen.findByText(/nothing to add up yet/i)).toBeInTheDocument();
    // 0.00 would be a claim. It must not be on the page.
    expect(screen.queryByText(/^0\.00/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\b0,00\b/)).not.toBeInTheDocument();
  });

  it("points at the account that has to exist first", async () => {
    renderAt("/finance/overview");
    expect(
      await screen.findByRole("link", { name: /register an account/i }),
    ).toHaveAttribute("href", "/finance/accounts");
  });

  it("never reaches for the demo on its own", async () => {
    const fetchMock = stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview");
    await screen.findByText(/nothing to add up yet/i);

    // Nothing invented is on screen, and the analytics endpoints really were
    // asked — the empty state is an answer, not an avoidance.
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/analytics/net-worth"))).toBe(
      true,
    );
    expect(screen.queryByText(/invented/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bDEMO\b/)).not.toBeInTheDocument();
  });

  it("leaves a month with no postings as a gap rather than a zero bar", async () => {
    renderAt("/finance/overview");
    expect(
      await screen.findByText(/a month with no postings is a gap, not a zero/i),
    ).toBeInTheDocument();
  });

it("marks an empty month at the floor with the word, and not as a short bar", async () => {
    /* The demo has one month with nothing in it. Three things were tried: nothing
       at all (an unlabelled hole), a dashed stub (a mark that looked like a bar
       and was not one), and this. The floor marker says where zero is and the
       word says the finding, and it sits on the same baseline as the five real
       bars — which is the part the dashed stub got wrong. */
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    const list = document.querySelector(
      "section[aria-labelledby='months-heading'] ul",
    ) as HTMLElement;
    const columns = [...list.querySelectorAll("li")];
    expect(columns).toHaveLength(6);

    /* One column has no movement. It carries a rule on the plot floor and the word
       "none", and it carries no bar. */
    const gap = columns.find((li) => li.getAttribute("aria-label")?.includes("no movement"));
    expect(gap).toBeDefined();
    /* The strip's only child. Read as a direct child rather than through a
       `div > *` selector, which returns null in this jsdom for no reason worth
       debugging. */
    const strip = gap?.querySelector("div") as HTMLElement;
    const gapMark = strip.firstElementChild as HTMLElement;
    expect(gapMark.tagName).toBe("SPAN");
    expect(gapMark.className).toContain("bg-ink/30");
    /* A 2px rule, not a height. A `height` here would be a bar. */
    expect(gapMark.getAttribute("style")).toBeNull();
    expect((gap?.textContent ?? "").replace(/\s+/g, " ")).toContain("none");

    /* Every other column has a real bar with a proportional height. */
    const bars = columns
      .filter((li) => li !== gap)
      .map((li) => (li.querySelector("div") as HTMLElement).firstElementChild as HTMLElement);
    expect(bars).toHaveLength(5);
    for (const bar of bars) {
      expect(bar.tagName).toBe("DIV");
      expect(bar.className).toContain("bg-ink");
      expect(bar.getAttribute("style")).toMatch(/height:/);
    }
  });

it("puts every bar on one baseline, empty month included", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* The defect this guards: the "none" caption made the empty month's column
       taller than its neighbours, and a bottom-aligned list then lifted that one
       strip off the shared baseline — so the absence read as a stub sitting above
       the rest. jsdom has no layout engine, so this asserts the cause: the list
       top-aligns its columns, which puts every strip at the same y regardless of
       how many lines a caption runs to. */
    const list = document.querySelector(
      "section[aria-labelledby='months-heading'] ul",
    ) as HTMLElement;
    expect(list.className).toContain("items-start");
    /* `items-end` is the thing that used to break it, and it must not come back. */
    expect(list.className).not.toContain("items-end");
  });
});

describe("overview — the demo is explicit or it does not happen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("shows invented figures when the URL asks for them, labelled once and loudly", async () => {
    stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview?demo=1");

    /* ONE marker, not four. An earlier draft said it in a banner, a chip, a
       paragraph and a badge inside the amount box, which is four chances to miss
       the one that matters and four things to read before using the page. */
    expect(await screen.findByText(/every figure below is invented/i)).toBeInTheDocument();
    expect(document.body.textContent?.match(/demo/gi) ?? []).toHaveLength(1);
    // The way out of it is in the same bar, so the recovery is where the
    // statement is.
    expect(screen.getByRole("button", { name: /show my ledger/i })).toBeInTheDocument();
    // …and the second control is gone rather than saying it again in a smaller voice.
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("never asks the API for the demo's numbers", async () => {
    const fetchMock = stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/analytics/"))).toBe(false);
  });

  it("goes back to the real figures when the banner's action is used, and drops the parameter", async () => {
    stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    await userEvent.click(screen.getByRole("button", { name: /show my ledger/i }));
    expect(await screen.findByText(/nothing to add up yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/every figure below is invented/i)).not.toBeInTheDocument();
    // The address bar and the screen cannot now disagree about a reload.
    expect(window.location.search).not.toContain("demo=1");
  });

  it("can be put back into demo from the page, off the empty state", async () => {
    stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview");
    await screen.findByText(/nothing to add up yet/i);
    // OFF, the control is a plain quiet button that says what it does.
    await userEvent.click(screen.getByRole("switch", { name: /show demo data/i }));
    expect(await screen.findByText(/every figure below is invented/i)).toBeInTheDocument();
  });
});

describe("overview — a failed read is loud", () => {
  beforeEach(() => {
    stubApi({
      ...EMPTY_HANDLERS(),
      "GET /analytics/net-worth": () => ({
        status: 500,
        body: { detail: "connection to server at lifeos-db failed" },
      }),
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("shows the server's own words, and not an empty state", async () => {
    renderAt("/finance/overview");
    /* Every panel that depends on the aggregates reports the failure, so the
       message appears once per panel rather than once per page. That is correct —
       a reader looking at the spend panel should not have to infer from the
       amount box that it also failed. */
    expect(
      await screen.findAllByText(/connection to server at lifeos-db failed/i),
    ).not.toHaveLength(0);
    expect(screen.queryByText(/nothing to add up yet/i)).not.toBeInTheDocument();
  });

  it("does not fall back to the demo to fill the gap", async () => {
    renderAt("/finance/overview");
    await screen.findAllByText(/connection to server at lifeos-db failed/i);
    expect(screen.queryByText(/invented/i)).not.toBeInTheDocument();
  });

  it("does not claim the review queue is clear when nobody read it", async () => {
    renderAt("/finance/overview");
    await screen.findAllByText(/connection to server at lifeos-db failed/i);
    // The queue is a different endpoint and it failed too, so the stub has to say
    // "unknown" rather than "0, nothing waiting".
    const stub = screen.getByRole("region", { name: /review queue/i });
    expect(within(stub).getByText(/unknown/i)).toBeInTheDocument();
    expect(within(stub).queryByText(/nothing waiting/i)).not.toBeInTheDocument();
  });

  it("offers a retry that re-asks", async () => {
    const fetchMock = stubApi({
      ...EMPTY_HANDLERS(),
      "GET /analytics/net-worth": () => ({ status: 500, body: { detail: "boom" } }),
    });
    renderAt("/finance/overview");
    await screen.findAllByRole("button", { name: /try again/i });
    const before = fetchMock.mock.calls.length;
    await userEvent.click(screen.getAllByRole("button", { name: /try again/i })[0]!);
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
  });
});

/** The net-worth panel's rendered figure text.
 *
 * Read off the container rather than matched with `getByText`, because the figure
 * is deliberately split across nodes so a sign can be hidden from the eye and not
 * from a screen reader — and an exact-text query cannot see across that split. */
const figureText = async (container: HTMLElement = document.body): Promise<string> => {
  const box = container.querySelector("section[aria-labelledby='net-worth-label']");
  if (box === null) {
    throw new Error("the net-worth panel is not on the page");
  }
  await waitFor(() => {
    const text = box.textContent ?? "";
    if (!/\d/.test(text)) {
      throw new Error("the amount box has no figure in it yet");
    }
  });
  return box.textContent ?? "";
};

describe("overview — figures", () => {
  beforeEach(() => {
    stubApi(POPULATED());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("shows the net worth in integer minor units, never as a float", async () => {
    renderAt("/finance/overview");
    /* 4_250_000 minor units is forty-two thousand five hundred euros, printed as
       42,500.00. The raw integer count has to be absent from the page as well as
       present in it: a figure that reached the screen as 4250000 would mean a
       float, or a scale error, somewhere between the wire and the DOM. */
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    expect(document.body.textContent).not.toContain("4250000");
    expect(document.body.textContent).not.toContain("42,500.000");
  });

  it("prints a delta against 30 days, not a remembered one", async () => {
    renderAt("/finance/overview");
    /* 4_250_000 less the last point at or before 5 September — which is the 1st,
       because the 20th is after the cutoff — is 250_000 minor units, printed
       2,500.00. The point chosen is the one at or before the cutoff, not the
       nearest one after it: comparing against a later balance would report a rise
       that the account did not have. */
    expect(await screen.findByText(/\+2,500\.00/)).toBeInTheDocument();
    expect(screen.getByText(/more than 30 days ago/i)).toBeInTheDocument();
  });

  it("carries the direction in words and in a sign, not only in colour", async () => {
    renderAt("/finance/overview");
    expect(await screen.findByText(/\+2,500\.00/)).toBeInTheDocument();
    expect(screen.getByText(/more than 30 days ago/i)).toBeInTheDocument();
    // The authored arrow is a shape, and it is an SVG rather than a Unicode glyph
    // — none of the three vendored faces carries U+2192, so a character would
    // silently fall back to whatever the OS has.
    const arrow = document.querySelector("svg[stroke-linecap='round']");
    expect(arrow).not.toBeNull();
  });

  it("gives every amount the monospace and the prose one face", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });

    /* Figures are never part of a prose decision. A column of money that does not
       align on the decimal point is a broken ledger, so every amount on the page
       is the tabulating face regardless of what the surrounding words are set in.
       The prose face was settled at Work Sans; this asserts the figure face was
       never in scope for that choice and has not drifted since. */
    const money = [...document.querySelectorAll(".font-mono")];
    expect(money.length).toBeGreaterThan(3);
    for (const el of money) {
      expect(el.textContent).toMatch(/\d/);
    }
    /* And there is no second figure face: nothing on the page opts out of the
       monospace for an amount. */
    const amounts = [...document.querySelectorAll(".tabular-nums")];
    expect(amounts.length).toBeGreaterThan(0);
    for (const el of amounts) {
      expect(el.className).toContain("font-mono");
    }
  });

  it("keeps the one-transaction category rather than dropping it as an outlier", async () => {
    renderAt("/finance/overview");
    /* −1,237 minor units is twelve thirty-seven, and it gets a row of its own with
     * its own figure rather than being merged into the leading category or hidden
     * as an outlier. The list stopping without saying so would look like a complete
     * answer. */
    expect(await screen.findByText("Apotheek")).toBeInTheDocument();
    expect(document.body.textContent).toContain("12.37");
    /* Scoped to the breakdown panel: `Huur` also appears in the leading panel's
     * top-category sentence, which is the point of that sentence — the headline is
     * attached to a row the reader can see below it. */
    const breakdown = within(
      document.querySelector("section[aria-labelledby='categories-heading']") as HTMLElement,
    );
    expect(breakdown.getByText("Huur")).toBeInTheDocument();
    expect(breakdown.getByText("Apotheek")).toBeInTheDocument();
  });

  it("states the unit once per panel and never on a figure", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    /* One convention, on every panel: the unit rides the panel's label and no
     * figure carries a code. Three were in play before this — a code on the hero,
     * a bare figure in the spend panel, a bare figure in the category list — and a
     * reader had to work out per panel which one they were looking at. */
    expect(screen.getByText(/spent · EUR/i)).toBeInTheDocument();
    expect(screen.getByText(/net worth · EUR/i)).toBeInTheDocument();
    expect(screen.getByText(/where it went/i).textContent).toMatch(/EUR out/);
    // The leading figure element itself is the digits and nothing else — the unit
    // lives on the label above it, not on the number.
    const figure = document.querySelector(".font-mono.tabular-nums");
    expect(figure?.textContent).toMatch(/^\d{1,3}(,\d{3})*\.\d{2}$/);
  });

  it("shows category magnitudes unsigned, and keeps the delta's sign", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    /* −118_900 is a magnitude of 1,189.00 — the heading already says the money
     * went out, and eight minus signs in a column repeat what the reader has not
     * forgotten. The delta is different: there the direction IS the content. */
    const breakdown = within(
      document.querySelector("section[aria-labelledby='categories-heading']") as HTMLElement,
    );
    expect(breakdown.getByText("Huur")).toBeInTheDocument();
    expect(breakdown.getByText("Huur").closest("li")?.textContent).not.toMatch(/[−-]\s*1,189/);
    /* The delta keeps its sign, and it is the only place on the page that does. */
    expect(document.body.textContent).toContain("+2,500.00");
  });
});

describe("overview — what leads, and why", () => {
  beforeEach(() => {
    stubApi(POPULATED());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  /* PRODUCT.md scopes V1 as spending and the mid-month use is "check my spending
     from the previous month". Net worth is `assets − liabilities` — the mechanism
     this surface proves, and still on the page — but with no savings or investment
     holdings it is an M10 idea with less to say, and leading with it put M10 in the
     loudest position on a V1 page. These tests hold that ordering. */
  it("puts spending before net worth in the reading order", async () => {
    renderAt("/finance/overview");
    await screen.findByText(/net worth · EUR/i);

    const sections = [...document.querySelectorAll("main section")];
    const labels = sections.map(
      (s) => s.getAttribute("aria-labelledby") ?? s.textContent?.slice(0, 24),
    );

    const spending = labels.findIndex((l) => /spend-heading|spent/i.test(l ?? ""));
    const netWorth = labels.findIndex((l) => /net-worth-label/i.test(l ?? ""));
    const series = labels.findIndex((l) => /series-heading/i.test(l ?? ""));

    expect(spending).toBeGreaterThanOrEqual(0);
    expect(spending).toBeLessThan(netWorth);
    /* And the six-month series follows the figure it belongs to, rather than
       floating somewhere else on the page. */
    expect(netWorth).toBeLessThan(series);
  });

  it("leads with spending, not a net-worth figure", async () => {
    /* The demo, deliberately: it is the state where the review stub is ALSO lime,
       because seven uncategorised payments are waiting. That is the only state in
       which the "one loud region" rule is actually load-bearing, and a rule only
       tested in the state where nothing competes is a rule that has not been
       tested. */
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* The first display-size figure on the page is the one spent. Not the most
       important number a person owns — the one they came for. */
    const lead = document.querySelector("section[aria-labelledby='spend-heading']");
    expect(lead).not.toBeNull();
    expect(lead?.className).toContain("bg-lime");

    /* LIME IS THE LOUD REGION, and that means the lead is the FIRST one and the only
       one carrying a display-size figure — not the only lime thing on the page. The
       review stub is also lime while work is waiting, because its fill is its state
       and a queue with seven items in it should not be cyan. It is perforated, it
       is detachable, and it carries a count rather than a headline figure, so it
       never competes. What would break the rule is a second lime PANEL above or
       beside this one. */
    const limes = [...document.querySelectorAll("main .bg-lime")];
    expect(limes.length).toBeGreaterThan(1);
    expect(limes[0]).toBe(lead);

    /* `offsetHeight` is 0 under jsdom, so the difference is asserted on the type
       rather than on the box: the loudness of this page is carried by figure size,
       and exactly one region on it holds a display-size one. */
    const displaySized = limes.filter((region) =>
      region.querySelector(".font-mono[class*='clamp(']"),
    );
    expect(displaySized).toHaveLength(1);
    expect(displaySized[0]).toBe(lead);

    /* Everything else lime is the stub, and nothing else. */
    for (const region of limes.slice(1)) {
      expect(region.getAttribute("aria-labelledby")).toBe("review-heading");
    }
  });

  it("gives the leading panel the four things it is allowed", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    const lead = document.querySelector("section[aria-labelledby='spend-heading']");
    expect(lead).not.toBeNull();
    const text = lead?.textContent ?? "";
    /* the amount, the comparison, the top category, and a way to the records. */
    expect(text).toMatch(/\d{1,3}(,\d{3})*\.\d{2}/);
    expect(text).toMatch(/%\s*more than|%less than|level with/i);
    expect(text).toMatch(/largest category across the months shown/i);
    expect(text).toMatch(/every transaction/i);
    /* The category it names carries its own window, because the figure above is one
       period and the breakdown covers the whole response. An earlier sentence said
       the top category was "of the amount above", which on the demo claimed a
       5,945.00 category was part of a 2,128.60 month. */
    expect(text).not.toMatch(/of the [\d,.]+ ?EUR above/i);
    /* …and not the account count, which belongs to the net-worth figure below. */
    expect(text).not.toMatch(/across \d+ accounts?/i);
    /* …and NOT the six-month bar chart. The brief asks for the single loud region,
       not the single tall one; the bars pushed this panel to about 640px and made
       it the biggest thing on the page as well as the loudest. They answer `when`
       rather than `how much` and now have a panel of their own. */
    expect(lead?.querySelector("ul")).toBeNull();
    expect(text).not.toMatch(/six months/i);
  });

  it("keeps the bar chart beside the lead rather than inside it", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* Six columns, one panel, and it is not the lime one. */
    const bars = document.querySelector("section[aria-labelledby='months-heading'] ul");
    expect(bars?.querySelectorAll("li")).toHaveLength(6);
    const months = document.querySelector("section[aria-labelledby='months-heading']");
    expect(months?.className).toContain("bg-panel");
    /* Not cyan: the category breakdown sits directly below it and two saturated
       fields stacked in one column is a stripe rather than a hierarchy, so the cyan
       stays unique in its column and lime the only saturated field in the other. */
    expect(months?.className).not.toContain("bg-cyan");
    expect(months?.className).not.toContain("bg-lime");
  });
});

describe("overview — the series axis says what it is doing", () => {
  beforeEach(() => {
    stubApi(POPULATED());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  /* A truncated axis is only honest if the truncation is visible. This chart is
     not anchored at zero and cannot be, so it says so in words, under the numbers,
     where a reader who did not notice the scale will still meet the fact. */
  it("states that the axis does not start at zero", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    expect(
      screen.getByText(/the axis runs .* and does not start at zero/i),
    ).toBeInTheDocument();
  });

  it("prints the axis bounds rather than the data's own extremes", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    /* The data here runs 40,000.00 to 42,500.00. An axis showing those as its
       bounds is the square wave — the defect this chart has been repaired for
       three times. A label showing a widened bound is a number the ledger never
       reported, so the bounds and the extremes are kept apart. */
    const axis = document.querySelector("section[aria-labelledby='series-heading']");
    expect(axis?.textContent).not.toMatch(/low\s+40,000\.00/i);
    expect(axis?.textContent).not.toMatch(/high\s+42,500\.00/i);
    /* The widened numbers are what the reader is shown, and the data's own
       extremes are named as the data's. */
    expect(axis?.textContent).toMatch(
      /net worth ran from 40,000\.00 EUR to 42,500\.00 EUR/i,
    );
  });

  it("draws no fill under the line, so no part of it reads as an empty rectangle", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* The area was tried twice and removed. With the axis at the data's own range it
       was a thin band under a line that filled the plot; with the axis widened to
       stop the line filling the plot it became a 40%-height grey slab standing on
       the axis floor, because a fill is bounded by the plot floor and that floor
       is necessarily far below where the value ever sits.

       A fill bounded to the data instead would need a second reference line that
       is neither zero nor the axis, and then an explanation of it — trading one
       thing to get wrong for two. Two paths and no `<defs>` is the whole chart. */
    const svg = document.querySelector(
      "section[aria-labelledby='series-heading'] svg[role='img']",
    );
    expect(svg?.querySelector("defs")).toBeNull();
    for (const path of [...(svg?.querySelectorAll("path") ?? [])]) {
      /* Every path is a stroke. `fill="none"` on all of them is the assertion; a
         region path would carry a fill reference or a colour instead. */
      expect(path.getAttribute("fill")).toBe("none");
    }
    expect(svg?.querySelectorAll("path")).toHaveLength(2);
    /* And nothing in the stylesheet reintroduces one. */
    expect(document.querySelector("[style*='series-wash']")).toBeNull();
  });

it("keeps the axis reasoning out of the reading path but keeps all of it", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* Five lines of methodology printed flat under a chart is the one thing a page
       someone opens to read a number cannot afford, however correct it is. The
       reasoning moves one click away; it is not shortened. */
    const reasoning = screen.getByText(/does not start at zero\. It has to/i);
    const details = reasoning.closest("details");
    expect(details).not.toBeNull();
    /* Closed by default — the reader chooses to read the methodology. */
    expect(details?.hasAttribute("open")).toBe(false);

    /* What is visible is one line, and it names the caveat rather than saying
       "More", so it can be found by the question it answers. */
    const summary = details?.querySelector("summary")?.textContent ?? "";
    expect(summary).toMatch(/not zero/i);
    expect(summary).toMatch(/dashed/i);
    expect(summary.length).toBeLessThan(160);

    /* Nothing of the essay is left outside the disclosure. */
    const panel = document.querySelector(
      "section[aria-labelledby='series-heading']",
    ) as HTMLElement;
    const outside = [...panel.children]
      .filter((child) => child.tagName !== "DETAILS")
      .map((child) => child.textContent ?? "")
      .join(" ");
    expect(outside).not.toMatch(/does not start at zero/i);
    expect(outside).not.toMatch(/no posting at all/i);
  });

it("gives the month bars the same treatment", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    const details = document.querySelector(
      "section[aria-labelledby='months-heading'] details",
    );
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
    /* The visible line names the caveat — a reader who sees a floor marker with no
       bar on it is told straight away that it means absence, not zero. */
    expect(details?.querySelector("summary")?.textContent ?? "").toMatch(
      /no bar because nothing posted/i,
    );
  });

it("dashes a stretch where nothing posted, rather than claiming it held still", async () => {
    renderAt("/finance/overview?demo=1");
    await screen.findByText(/every figure below is invented/i);

    /* `role="img"` is the chart and only the chart — the section also holds a
       link whose arrow is an `aria-hidden` decorative SVG, and a bare `svg`
       selector would pick that one up first. */
    const svg = document.querySelector(
      "section[aria-labelledby='series-heading'] svg[role='img']",
    );
    expect(svg).not.toBeNull();
    /* The demo ledger has posting-free stretches — one whole month with nothing in
       it — and a solid line across them would say "net worth was constant", which
       the ledger does not claim. Nothing posted. So the held segments are drawn
       dashed and separately from the observed line. */
    const paths = [...(svg?.querySelectorAll("path") ?? [])].map((p) => ({
      dash: p.getAttribute("stroke-dasharray"),
      fill: p.getAttribute("fill"),
      opacity: p.getAttribute("stroke-opacity"),
    }));

    const dashed = paths.filter((p) => p.dash !== null);
    expect(dashed).toHaveLength(1);
    expect(dashed[0]?.opacity).toBe("0.5");
    /* The solid line is a separate path, so the two are never confused. */
    expect(paths.filter((p) => p.dash === null && p.fill === "none")).toHaveLength(1);
    expect(screen.getByText(/passed with no posting at all/i)).toBeInTheDocument();
  });
});

describe("overview — the prose face is decided, not offered", () => {
  beforeEach(() => {
    stubApi(EMPTY_HANDLERS());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  /* The toggle existed because the choice of prose face was open and could only be
     made by looking at the rendered page. It has now been made — Work Sans, x/cap
     0.52, over the 0.73 alternative that competed with the mono figures — so there
     is no decision left for a control to hold. These tests exist to keep it
     deleted: a control that offers a settled choice implies the choice is still
     open, and an unused font shipped in the bundle is weight nobody benefits
     from. */
  it("ships no control for choosing a prose face", async () => {
    renderAt("/finance/overview");
    await screen.findByText(/nothing to add up yet/i);
    expect(screen.queryByRole("radiogroup", { name: /prose face/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("never names the face that was rejected", async () => {
    renderAt("/finance/overview");
    await screen.findByText(/nothing to add up yet/i);
    expect(document.body.textContent ?? "").not.toMatch(/inter/i);
  });

  it("stamps no face decision onto the document", async () => {
    /* The mechanism was a `data-prose` attribute on `<html>` read by a
       `[data-prose]` override in the stylesheet. With one face there is nothing to
       override, so the attribute is not written at all — an attribute that always
       holds the same value is a second place to go wrong. */
    renderAt("/finance/overview");
    await screen.findByText(/nothing to add up yet/i);
    expect(document.documentElement.dataset.prose).toBeUndefined();
  });
});

describe("overview — the review stub is a state, not a decoration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("is lime when there is something to clear, and cyan when there is not", async () => {
    /* Two money-out transactions, only one of which has a category. The count IS
       the queue, and the queue is uncategorised money out — so this is 1, and a
       panel that said 2 would be counting a row that is not waiting for anything. */
    stubApi({
      ...EMPTY_HANDLERS(),
      "GET /transactions": () => ({
        status: 200,
        body: [TRANSACTION(), TRANSACTION({ id: 2, category_id: 3 })],
      }),
    });
    const { unmount } = renderAt("/finance/overview");
    /* Re-queried rather than held onto a reference: the stub's `key` includes its
       state, so React REPLACES the element when the state changes. A captured
       reference would be a detached node that still reads cyan — which is exactly
       what a stale-node assertion looks like when the page is actually right. */
    const stub = await waitFor(() => {
      const node = screen.getByRole("region", { name: /review queue/i });
      expect(node.className).toContain("bg-lime");
      return node;
    });
    expect(within(stub).getByText(/Waiting/)).toBeInTheDocument();
    /* The count and its label are deliberately separate elements — a figure in
       the mono and a sentence in the prose — so the assertion is made on the
       panel's own text rather than on a node that spans both. */
    expect(stub.textContent).toMatch(/1\s*transaction needs a category/);
    unmount();

    stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview");
    /* Waiting on `bg-cyan` alone would pass in the UNKNOWN state too — cyan is
       what the panel shows both when the queue is clear and when nobody has read
       it. The word is what distinguishes them, so the word is what is waited on.
       That is the entire reason the stub carries three carriers for its state
       instead of one. */
    const cleared = await waitFor(() => {
      const node = screen.getByRole("region", { name: /review queue/i });
      expect(node.className).toContain("bg-cyan");
      expect(within(node).getByText("Clear")).toBeInTheDocument();
      return node;
    });
    expect(cleared.textContent).toMatch(/0\s*nothing waiting/);
  });

  it("keeps one perforated profile in both states", async () => {
    // The mask is on the same element in both states; only the fill changes. This
    // is asserted in the browser audit (tools/audit.mjs) because a computed mask
    // is not observable from jsdom — here it is at least pinned in the class.
    stubApi(POPULATED());
    renderAt("/finance/overview");
    const stub = await screen.findByRole("region", { name: /review queue/i });
    expect(stub.className).toContain("stub-edge");
  });

  it("says the import is not scheduled rather than inventing a date", async () => {
    stubApi(EMPTY_HANDLERS());
    renderAt("/finance/overview");
    const stub = await screen.findByRole("region", { name: /review queue/i });
    await waitFor(() =>
      expect(within(stub).getByText(/is not scheduled/i)).toBeInTheDocument(),
    );
  });
});

describe("overview — the page's shape", () => {
  beforeEach(() => {
    stubApi(POPULATED());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("has no kicker or eyebrow above any heading", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    for (const heading of screen.getAllByRole("heading")) {
      const previous = heading.previousElementSibling;
      expect(previous?.textContent ?? "").not.toMatch(
        /^(net worth|spent|review queue|where it went|life os)/i,
      );
    }
  });

  it("puts every small-caps label BESIDE a label, not above a heading", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    /* The ban is on a kicker ABOVE a heading. `Net worth · EUR` and `As at …` are
     * two fields of one form, side by side, and the second is a caption naming the
     * date the balance is as of — which a reader cannot derive from the figure and
     * is the difference between a number and a claim about a moment. */
    const box = document.querySelector("section[aria-labelledby='net-worth-label']");
    const label = box?.querySelector("#net-worth-label");
    expect(label?.parentElement).toBe(box?.querySelector("div"));
    // The date is a sibling of the label, not stacked above it.
    expect(label?.parentElement?.querySelectorAll("p").length).toBe(2);
  });

  it("opens on a figure rather than on an introduction", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    // No greeting, no avatar, no paragraph standing in for one.
    expect(screen.queryByText(/hello|welcome|good (morning|afternoon)/i)).not.toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("keeps one h1 on the page", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("uses no shadow and no border on any panel", async () => {
    renderAt("/finance/overview");
    await waitFor(async () => {
      expect(await figureText()).toContain("42,500.00");
    });
    /* The WORLD carries the flatness, and this is where it is caught. The
       stylesheet has no shadow token at all and no panel carries a border
       utility — so a shadow creeping back in shows up as a new class on a
       `section`, which is exactly what a reviewer can catch without opening
       devtools. (The computed `box-shadow` itself needs a layout engine, and is
       measured in a real browser by `tools/audit.mjs`.) */
    const panels = document.querySelectorAll("section");
    expect(panels.length).toBeGreaterThan(3);
    for (const panel of panels) {
      const cls = panel.className ?? "";
      expect(cls).not.toMatch(/shadow(-|\[|\s)/);
      expect(cls).not.toMatch(/(^|\s)border(-|\[|\s|$)/);
      expect(cls).toMatch(/rounded/);
    }
  });
});