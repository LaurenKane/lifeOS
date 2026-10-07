/**
 * The away strip (Q8): "you were away N days — do these still matter?"
 *
 * Three claims, in order of the decisions they encode: the strip speaks its
 * line (and only when two or more days), the phrasing grounds rather than
 * shames, and the day's ack is ONE tap — its POST is on the wire because
 * server-side `acked_today` is what makes the next reload quiet, and a local
 * state alone would re-nag on refresh forever.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const TODAY_BOARD = {
  do_now: [ACTION],
  kept_back: 0,
  cap: 5,
  urgent: [],
  upcoming: [],
  upkeep_opportunities: [],
  inbox: { count: 1, stale: 0 },
} as const;

const CATCH_UP = {
  away_days: 5,
  acked_today: false,
  goals: [
    {
      goal_id: 3,
      title: "learn guitar",
      area: "hobbies",
      current_focus: "chord shapes",
    },
  ],
  thoughts: [
    { thought_id: 7, text: "maybe a case", created_at: "2026-10-02T08:00:00Z" },
  ],
} as const;

/** Paths answer per endpoint; the catch-up strip is the only non-empty
 * life read here, everything else stubs to its empty shape. The ack is
 * stateful because the page's quiet-forever rest is the SERVER's
 * `acked_today` — a local flag would not survive the reload. */
const stubFetch = () => {
  let acked = false;
  const fetchMock = vi.fn((input: unknown) => {
    const path = String(input);
    const method = (input as { method?: string }).method ?? "GET";
    if (path.includes("/v1/life/catchup/ack")) {
      acked = true;
    }
    const body: unknown = path.includes("/v1/life/catchup")
      ? { ...CATCH_UP, acked_today: acked }
      : path.includes("/v1/life/today")
        ? TODAY_BOARD
        : [];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
      method,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const renderDashboard = () => {
  const router = createMemoryRouter(
    [{ path: "/", element: <DashboardPage /> }],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
};

describe("the away strip", () => {
  let fetchMock: ReturnType<typeof stubFetch>;

  beforeEach(() => {
    fetchMock = stubFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("speaks the away line and the question, grounded", async () => {
    renderDashboard();

    expect(await screen.findByText(/you were away 5 days/i)).toBeInTheDocument();
    expect(
      screen.getByText(/do these still matter\?/i),
    ).toBeInTheDocument();
    expect(screen.getByText("learn guitar")).toBeInTheDocument();
  });

  it("the day's ack posts once to the ack path, then the strip rests", async () => {
    const user = userEvent.setup();
    renderDashboard();

    await user.click(
      await screen.findByRole("button", {
        name: /i've seen this — go quiet for today/i,
      }),
    );

    const acks = fetchMock.mock.calls.filter(
      (call) =>
        String(call[0]).includes("/api/v1/life/catchup/ack") &&
        (call[1] as RequestInit).method === "POST",
    );
    expect(acks).toHaveLength(1);

    /* Wait for the refetch the ack triggers before asserting unmount,
     * or a still-in-flight strip would look stripped when it is not. */
    await waitFor(() => {
      expect(screen.queryByText(/you were away 5 days/i)).not.toBeInTheDocument();
    });
  });
});
