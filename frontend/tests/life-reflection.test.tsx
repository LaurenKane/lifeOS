/**
 * The "Looking back" panel: the deterministic reflection read.
 *
 * Two claims: when something happened the panel speaks its exact grammar
 * ("{N} small steps on {title}", "kept going N times"), and when nothing
 * happened the panel does not mount — the words "Looking back" are absent,
 * with no empty-state box standing in for silence.
 */
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "@/features/life/dashboard/page";

const TODAY_BOARD = {
  do_now: [],
  kept_back: 0,
  cap: 5,
  urgent: [],
  upcoming: [],
  upkeep_opportunities: [],
  inbox: { count: 0, stale: 0 },
} as const;

/** The away strip must stay off in these tests: zero days away and already
 * acked is the shape whose strip never mounts. */
const CATCH_UP_QUIET = {
  away_days: 0,
  acked_today: true,
  goals: [],
  thoughts: [],
} as const;

const REFLECTION_ONE_OF_EACH = {
  window_days: 30,
  goals: [
    {
      goal_id: 5,
      title: "learn guitar",
      area: "hobbies",
      count: 2,
      done_texts: ["put the desk together", "change strings"],
    },
  ],
  upkeeps: [{ upkeep_id: 9, title: "water the plants", count: 3 }],
} as const;

const REFLECTION_EMPTY = {
  window_days: 30,
  goals: [],
  upkeeps: [],
} as const;

/** Paths answer per endpoint; the reflection body is the only non-empty
 * non-board read here, everything else stubs to its empty shape. */
const stubFetchWith = (reflectionBody: unknown) => {
  const fetchMock = vi.fn((input: unknown) => {
    const path = String(input);
    const body: unknown = path.includes("/v1/life/reflection")
      ? reflectionBody
      : path.includes("/v1/life/catchup")
        ? CATCH_UP_QUIET
        : path.includes("/v1/life/today")
          ? TODAY_BOARD
          : [];
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

const renderDashboard = () => {
  const router = createMemoryRouter(
    [{ path: "/", element: <DashboardPage /> }],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
};

describe("the reflection panel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("speaks the month's facts when something happened", async () => {
    stubFetchWith(REFLECTION_ONE_OF_EACH);
    renderDashboard();

    expect(await screen.findByText("Looking back")).toBeInTheDocument();
    expect(screen.getByText(/2 small steps on learn guitar/i)).toBeInTheDocument();
    expect(screen.getByText(/3 times/i)).toBeInTheDocument();
    /* The quiet quotes: the actual done words, not dates as headlines. */
    expect(screen.getByText(/put the desk together/i)).toBeInTheDocument();
    expect(screen.getByText(/change strings/i)).toBeInTheDocument();
  });

  it("renders silence when the month has nothing", async () => {
    stubFetchWith(REFLECTION_EMPTY);
    renderDashboard();

    /* The board's own data has settled before this probe is meaningful. */
    expect(await screen.findByText("Nothing assigned to today — good.")).toBeInTheDocument();
    expect(screen.queryByText("Looking back")).not.toBeInTheDocument();
  });
});
