/* The life module's typed API calls.
 *
 * Every function returns ONE parse-checked promise or fails with the standard
 * ApiError. Dates come back as ISO strings and are handed to the API the same
 * way; nothing here re-signs amounts or re-derives meanings.
 */
import { API_PATH, apiDelete, apiGet, apiPatch, apiPost, expectSchema } from "@/lib/apiClient";
import {
  ActionSummary,
  CaptureResponse,
  CatchUpSummary,
  GoalSummary,
  PixelDay,
  ReflectionSummary,
  ThoughtSummary,
  TodayBoard,
  UpkeepSummary,
  VisionItemSummary,
  WishlistItemSummary,
  type Area,
} from "./types";

const P = `${API_PATH}/life`;

/* ── Capture ─────────────────────────────────────────────────────────────── */

export const capture = (text: string): Promise<CaptureResponse> =>
  apiPost<{ text: string }, CaptureResponse>(`${P}/capture`, { text });

export const undoCapture = (thoughtId: number): Promise<unknown> =>
  apiDelete(`${P}/capture/${thoughtId}`);

export const resolveThought = (
  thoughtId: number,
  body:
    | { choice: "action" | "goal" | "rests" | "dismissed" }
    | {
        choice: "upkeep";
        upkeep_id?: number;
        create_receipt?: boolean;
        aim_days?: number;
      },
): Promise<{ thought: ThoughtSummary }> =>
  apiPost<unknown, { thought: ThoughtSummary }>(
    `${P}/thoughts/${thoughtId}/resolve`,
    body,
  );

export const listThoughts = (): Promise<ThoughtSummary[]> =>
  apiGet<ThoughtSummary[]>(`${P}/thoughts`);

/* ── The board ───────────────────────────────────────────────────────────── */

/* The board is the one read parsed here rather than cast: it is the screen the
 * app opens on, and a response that is not a TodayBoard has to say so at the
 * boundary instead of reaching a render as `undefined.do_now`. The failure lands
 * in the hook's own error state, which the Dashboard already shows as a Notice. */
export const todayBoard = (): Promise<TodayBoard> =>
  apiGet<unknown>(`${P}/today`).then((value) =>
    expectSchema(TodayBoard, value, "today board"),
  );

export const pixels = (): Promise<PixelDay[]> => apiGet<PixelDay[]>(`${P}/pixels`);

/* ── The away strip ──────────────────────────────────────────────────────── */

/* Parsed like the board: the strip is one screen the app opens on, and a
 * response that is not a CatchUpSummary must say so at the boundary. */
export const catchUp = (): Promise<CatchUpSummary> =>
  apiGet<unknown>(`${P}/catchup`).then((value) =>
    expectSchema(CatchUpSummary, value, "catch-up summary"),
  );

export const ackCatchUp = (day: string): Promise<{ acked: boolean }> =>
  apiPost<{ day: string }, { acked: boolean }>(`${P}/catchup/ack`, { day });

/* ── Looking back (reflection) ─────────────────────────────────────────── */

/* Parsed like the board: a response that is not a ReflectionSummary must say
 * so at the boundary, not surface as `undefined.goals` in a render. An empty
 * summary (both lists empty) is a normal answer the Dashboard renders as
 * silence — not an empty state. */
export const reflection = (): Promise<ReflectionSummary> =>
  apiGet<unknown>(`${P}/reflection`).then((value) =>
    expectSchema(ReflectionSummary, value, "reflection summary"),
  );

/* ── Actions ─────────────────────────────────────────────────────────────── */

export const completeAction = (id: number): Promise<ActionSummary> =>
  apiPost<undefined, ActionSummary>(`${P}/actions/${id}/done`, undefined);

export const reopenAction = (id: number): Promise<ActionSummary> =>
  apiPost<undefined, ActionSummary>(`${P}/actions/${id}/undone`, undefined);

export const planAction = (id: number, day: string): Promise<ActionSummary> =>
  apiPatch<{ planned_date: string }, ActionSummary>(
    `${P}/actions/${id}`,
    { planned_date: day },
  );

export const editAction = (
  id: number,
  body: { text?: string; due_date?: string; urgent?: boolean; goal_id?: number },
): Promise<ActionSummary> => apiPatch<typeof body, ActionSummary>(
  `${P}/actions/${id}`,
  body,
);

/* ── Goals ───────────────────────────────────────────────────────────────── */

export const listGoals = (): Promise<GoalSummary[]> =>
  apiGet<GoalSummary[]>(`${P}/goals`);

export const createGoal = (body: {
  title: string;
  why?: string;
  area?: Area;
  minimum?: string;
  current_focus?: string;
}): Promise<GoalSummary> => apiPost<typeof body, GoalSummary>(`${P}/goals`, body);

export const updateGoal = (
  id: number,
  body: {
    title?: string;
    why?: string;
    area?: Area;
    minimum?: string;
    current_focus?: string;
  },
): Promise<GoalSummary> => apiPatch<typeof body, GoalSummary>(
  `${P}/goals/${id}`,
  body,
);

export const pauseGoal = (id: number): Promise<GoalSummary> =>
  apiPost<undefined, GoalSummary>(`${P}/goals/${id}/pause`, undefined);

export const activateGoal = (id: number): Promise<GoalSummary> =>
  apiPost<undefined, GoalSummary>(`${P}/goals/${id}/activate`, undefined);

export const listWishlist = (goalId: number): Promise<WishlistItemSummary[]> =>
  apiGet<WishlistItemSummary[]>(`${P}/goals/${goalId}/wishlist`);

export const createWishlistItem = (
  goalId: number,
  text: string,
): Promise<WishlistItemSummary> =>
  apiPost<{ text: string }, WishlistItemSummary>(
    `${P}/goals/${goalId}/wishlist`,
    { text },
  );

export const tickWishlistItem = (
  goalId: number,
  itemId: number,
  isDone: boolean,
): Promise<WishlistItemSummary> =>
  apiPatch<{ is_done: boolean }, WishlistItemSummary>(
    `${P}/goals/${goalId}/wishlist/${itemId}`,
    { is_done: isDone },
  );

/* ── Upkeep ──────────────────────────────────────────────────────────────── */

/* `include_inactive` is the difference between the two lists the Upkeep page
 * shows: the board's own read skips resting upkeeps, and the disclosure that
 * holds them asks for the whole table. The summary schema carries no
 * `is_active`, so the caller derives which is which by id rather than
 * guessing from a field the server never sends. */
export const listUpkeeps = (includeInactive = false): Promise<UpkeepSummary[]> =>
  apiGet<UpkeepSummary[]>(
    `${P}/upkeeps${includeInactive ? "?include_inactive=true" : ""}`,
  );

export const createUpkeep = (body: {
  title: string;
  aim_days?: number;
}): Promise<UpkeepSummary> => apiPost<typeof body, UpkeepSummary>(`${P}/upkeeps`, body);

export const updateUpkeep = (
  id: number,
  body: { title?: string; aim_days?: number | null; is_active?: boolean },
): Promise<UpkeepSummary> =>
  apiPatch<typeof body, UpkeepSummary>(`${P}/upkeeps/${id}`, body);

export const recordReceipt = (
  upkeepId: number,
  receiptedAt?: string,
): Promise<{ receipted: number }> =>
  apiPost<{ receipted_at?: string }, { receipted: number }>(
    `${P}/upkeeps/${upkeepId}/receipts`,
    receiptedAt === undefined ? {} : { receipted_at: receiptedAt },
  );

/* ── Vision ──────────────────────────────────────────────────────────────── */

export const listVisionItems = (): Promise<VisionItemSummary[]> =>
  apiGet<VisionItemSummary[]>(`${P}/vision/items`);

export const addVisionPhrase = (
  text: string,
  area?: Area,
): Promise<VisionItemSummary> =>
  apiPost<{ text: string; area?: Area }, VisionItemSummary>(
    `${P}/vision/items`,
    { text, area },
  );

export const addVisionImage = (
  file: File,
  area?: Area,
): Promise<VisionItemSummary> => {
  /* The one multipart request in the life module: a file cannot travel as
   * JSON, so the standard `request` content-type header is overridden and the
   * browser sets the boundary. */
  const form = new FormData();
  form.append("file", file);
  const query = area === undefined ? "" : `?area=${encodeURIComponent(area)}`;
  const path = `${P}/vision/items/upload${query}`;
  return fetch(`${import.meta.env.VITE_API_BASE_URL ?? "/api"}${path}`, {
    method: "POST",
    body: form,
  }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `upload failed (${res.status})`);
    }
    return (await res.json()) as VisionItemSummary;
  });
};

export const updateVisionItem = (
  id: number,
  body: { text?: string; area?: Area; is_active?: boolean },
): Promise<VisionItemSummary> =>
  apiPatch<typeof body, VisionItemSummary>(`${P}/vision/items/${id}`, body);

export const deleteVisionItem = (id: number): Promise<void> =>
  apiDelete(`${P}/vision/items/${id}`);

export const mediaUrl = (mediaPath: string): string =>
  `${import.meta.env.VITE_API_BASE_URL ?? "/api"}${P}/vision/media/${mediaPath}`;
