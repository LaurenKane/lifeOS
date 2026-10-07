/**
 * The Dashboard — the screen the app opens on (ADR 0012).
 *
 * It composes both modules: the life half is a TodayBoard from
 * `/v1/life/today`, and the money half is two analytics reads from finance's
 * own endpoints. So the stub answers per path rather than with one blanket
 * shape: the board is an OBJECT (an array where a TodayBoard is promised is a
 * contract mismatch, and the page must say so rather than crash on
 * `undefined.do_now`), and the finance collections are what they always are —
 * empty arrays.
 *
 * Two assertions, because they are the two claims the front door makes: there
 * is a "Do now" panel, and there is one place to put a thought.
 */
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "@/features/life/dashboard/page";

const ACTION = {
  id: 1,
  text: "book the dentist",
  goal_id: null,
  parent_action_id: null,
  due_date: null,
  planned_date: null,
  urgent: false,
  is_done: false,
  done_at: null,
  created_at: "2026-10-06T09:00:00Z",
} as const;

const UPKEEP = {
  id: 2,
  title: "water the plants",
  aim_days: 7,
  last_done_at: "2026-10-01T18:00:00Z",
  earned_cadence_days: 7,
  next_opportunity: "2026-10-08",
  created_at: "2026-09-01T09:00:00Z",
} as const;

const TODAY_BOARD = {
  do_now: [ACTION],
  kept_back: 2,
  cap: 5,
  urgent: [],
  upcoming: [],
  upkeep_opportunities: [UPKEEP],
  inbox: { count: 1, stale: 0 },
} as const;

/** One stub, two answers: the life board is an object, finance is `[]`. */
const stubFetch = () => {
  const fetchMock = vi.fn((input: unknown) => {
    const path = String(input);
    const body: unknown = path.includes("/v1/life/today") ? TODAY_BOARD : [];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

/** The Dashboard at `/`, with no providers: it composes reads, it does not
 * consume a context, which is the whole point of putting it at the app layer. */
const renderDashboard = () => {
  const router = createMemoryRouter(
    [{ path: "/", element: <DashboardPage /> }],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
};

describe("life dashboard", () => {
  let fetchMock: ReturnType<typeof stubFetch>;

  beforeEach(() => {
    fetchMock = stubFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the Do now panel and the capture box", async () => {
    renderDashboard();

    expect(
      await screen.findByRole("heading", { name: "Do now" }),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("What's on your mind?"),
    ).toBeInTheDocument();
  });

  it("reads the board from life and the figures from finance", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Do now" });

    const paths = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(paths.some((path) => path.includes("/api/v1/life/today"))).toBe(true);
    expect(paths.some((path) => path.includes("/api/v1/analytics/net-worth"))).toBe(true);
  });
});
