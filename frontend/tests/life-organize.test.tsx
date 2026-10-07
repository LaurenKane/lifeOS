/**
 * The inbox helper ("help me sort the pile").
 *
 * Four claims, in the order the echo-then-tap design demands: the button
 * exists ONLY when the backend reports the helper available (an unconfigured
 * endpoint means the feature is absent, not off-and-waiting); a tap is ONE
 * call and the answer is suggestion rows, never applied rows; each row
 * carries apply / no thanks, and "no thanks" drops the row without the
 * pile changing (no server tap, no silent resolution).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InboxPage } from "@/features/life/inbox/page";

const THOUGHT = {
  id: 7,
  text: "maybe a case",
  resolved_kind: null,
  action_id: null,
  goal_id: null,
  upkeep_id: null,
  created_at: "2026-10-06T09:00:00Z",
} as const;

const SUGGESTIONS = {
  suggestions: [
    {
      thought_id: 7,
      choice: "action",
      due_date: "2026-10-09",
      urgent: false,
      why: "the text names a concrete chore",
    },
  ],
} as const;

const resolveResponse = {
  thought: { ...THOUGHT },
  created_action: null,
} as const;

/** The helper's availability rides the thoughts GET as a header
 * (`X-Helper-Available`), so the mock answers it — the body stays the list
 * shape every other consumer is pinned to. */
const stubFetch = (options: { helper?: boolean; suggestions?: boolean } = {}) => {
  const available = options.helper ?? false;
  const fetchMock = vi.fn((input: unknown) => {
    const path = String(input);
    let body: unknown = [];
    if (path.includes("/thoughts/organize")) {
      body = options.suggestions === false ? { suggestions: [] } : SUGGESTIONS;
    } else if (path.endsWith("/v1/life/thoughts")) {
      body = [THOUGHT];
    } else {
      body = resolveResponse;
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
      headers: { get: (name: string) => (name === "X-Helper-Available" ? String(available) : null) },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

const renderInbox = () => {
  const router = createMemoryRouter(
    [{ path: "/life/inbox", element: <InboxPage /> }],
    { initialEntries: ["/life/inbox"] },
  );
  return render(<RouterProvider router={router} />);
};

describe("the inbox helper", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("the button exists only when the helper is available", async () => {
    stubFetch({ helper: true });
    renderInbox();

    expect(
      await screen.findByRole("button", { name: /help me sort the pile/i }),
    ).toBeInTheDocument();
  });

  it("no button anywhere when the helper is unconfigured", async () => {
    stubFetch({ helper: false });
    renderInbox();

    expect(await screen.findByText("maybe a case")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /help me sort the pile/i })).not.toBeInTheDocument();
  });

  it("a tap is one organize call, and the answer is rows with apply and no thanks", async () => {
    const fetchMock = stubFetch({ helper: true, suggestions: true });
    const user = userEvent.setup();
    renderInbox();

    await user.click(
      await screen.findByRole("button", { name: /help me sort the pile/i }),
    );

    const organizeCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes("/thoughts/organize"),
    );
    expect(organizeCalls).toHaveLength(1);

    expect(
      await screen.findByText(/become an action — due 2026-10-09/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /apply/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /no thanks/i })).toBeInTheDocument();
  });

  it("no thanks drops the row — the pile untouched, no resolve call", async () => {
    const fetchMock = stubFetch({ helper: true, suggestions: true });
    const user = userEvent.setup();
    renderInbox();

    await user.click(
      await screen.findByRole("button", { name: /help me sort the pile/i }),
    );
    await user.click(await screen.findByRole("button", { name: /no thanks/i }));

    await waitFor(() => {
      expect(screen.queryByText(/become an action/i)).not.toBeInTheDocument();
    });
    expect(screen.getByText("maybe a case")).toBeInTheDocument();
    const resolveCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes("/resolve"),
    );
    expect(resolveCalls).toHaveLength(0);
  });
});
