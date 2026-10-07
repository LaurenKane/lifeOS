/* The life module's response contract, mirrored from the backend's OpenAPI.

 * `life.api.schemas` is the source of truth; these Zod schemas are the
 * hand-maintained frontend mirror (the same arrangement finance's types use —
 * nothing here is generated). Parsing is what makes them one: a date that
 * arrives as null where a string was promised becomes a stated mismatch at the
 * boundary instead of an `Undefined` deep in a render, which is the failure
 * that eats a screen.
 */
import { z } from "zod";

export const Area = z.enum([
  "home", "hobbies", "body", "career", "money", "people", "growth", "experiences",
]);
export type Area = z.infer<typeof Area>;

export const AREAS: Area[] = ["home", "hobbies", "body", "career", "money", "people", "growth", "experiences"];

export const ThoughtResolution = z.enum(["action", "goal", "upkeep", "rests", "dismissed"]);
export type ThoughtResolution = z.infer<typeof ThoughtResolution>;

export const ThoughtSummary = z.object({
  id: z.number(),
  text: z.string(),
  resolved_kind: ThoughtResolution.nullable(),
  action_id: z.number().nullable(),
  goal_id: z.number().nullable(),
  upkeep_id: z.number().nullable(),
  created_at: z.string(),
});
export type ThoughtSummary = z.infer<typeof ThoughtSummary>;

export const ActionSummary = z.object({
  id: z.number(),
  text: z.string(),
  goal_id: z.number().nullable(),
  parent_action_id: z.number().nullable(),
  due_date: z.string().nullable(),
  planned_date: z.string().nullable(),
  urgent: z.boolean(),
  is_done: z.boolean(),
  done_at: z.string().nullable(),
  created_at: z.string(),
});
export type ActionSummary = z.infer<typeof ActionSummary>;

export const GoalSummary = z.object({
  id: z.number(),
  title: z.string(),
  why: z.string().nullable(),
  area: Area.nullable(),
  minimum: z.string().nullable(),
  current_focus: z.string().nullable(),
  is_active: z.boolean(),
  created_at: z.string(),
});
export type GoalSummary = z.infer<typeof GoalSummary>;

export const UpkeepSummary = z.object({
  id: z.number(),
  title: z.string(),
  aim_days: z.number().nullable(),
  last_done_at: z.string().nullable(),
  earned_cadence_days: z.number().nullable(),
  next_opportunity: z.string().nullable(),
  created_at: z.string(),
});
export type UpkeepSummary = z.infer<typeof UpkeepSummary>;

export const VisionKind = z.enum(["image", "phrase"]);
export type VisionKind = z.infer<typeof VisionKind>;

export const VisionItemSummary = z.object({
  id: z.number(),
  kind: VisionKind,
  text: z.string().nullable(),
  media_path: z.string().nullable(),
  area: Area.nullable(),
  is_active: z.boolean(),
});
export type VisionItemSummary = z.infer<typeof VisionItemSummary>;

export const WishlistItemSummary = z.object({
  id: z.number(),
  goal_id: z.number(),
  text: z.string(),
  is_done: z.boolean(),
});
export type WishlistItemSummary = z.infer<typeof WishlistItemSummary>;

/** The Inbox line's numbers. `stale` is the fourteen-day subset — the only
 * subset the nudge counts, and deliberately the only count the Dashboard
 * shows: the pile's size is never an accusation, only a fact. */
export const InboxStatus = z.object({
  count: z.number().int(),
  stale: z.number().int(),
});

export const TodayBoard = z.object({
  do_now: z.array(ActionSummary),
  kept_back: z.number(),
  cap: z.number(),
  urgent: z.array(ActionSummary),
  upcoming: z.array(ActionSummary),
  upkeep_opportunities: z.array(UpkeepSummary),
  inbox: InboxStatus,
});
export type TodayBoard = z.infer<typeof TodayBoard>;

/** The away strip's one read (Q8): a fact about attention, never a score.
 * The Dashboard is the only consumer; this is the hand mirror of
 * `life.api.schemas.CatchUpSummary`. */
export const CatchUpThought = z.object({
  thought_id: z.number(),
  text: z.string(),
  created_at: z.string(),
});
export type CatchUpThought = z.infer<typeof CatchUpThought>;

export const CatchUpGoal = z.object({
  goal_id: z.number(),
  title: z.string(),
  area: Area.nullable(),
  current_focus: z.string().nullable(),
});
export type CatchUpGoal = z.infer<typeof CatchUpGoal>;

export const CatchUpSummary = z.object({
  away_days: z.number().int(),
  acked_today: z.boolean(),
  goals: z.array(CatchUpGoal),
  thoughts: z.array(CatchUpThought),
});
export type CatchUpSummary = z.infer<typeof CatchUpSummary>;

export const PixelDay = z.object({
  date: z.string(),
  count: z.number(),
});
export type PixelDay = z.infer<typeof PixelDay>;

/** The "Looking back" read: the rolling last 30 days as signed facts. The
 * hand mirror of `life.api.schemas.ReflectionSummary`; `done_texts` are
 * quiet example quotes, never dated headlines. */
export const ReflectionGoal = z.object({
  goal_id: z.number(),
  title: z.string(),
  area: Area.nullable(),
  count: z.number().int(),
  done_texts: z.array(z.string()),
});
export type ReflectionGoal = z.infer<typeof ReflectionGoal>;

export const ReflectionUpkeep = z.object({
  upkeep_id: z.number(),
  title: z.string(),
  count: z.number().int(),
});
export type ReflectionUpkeep = z.infer<typeof ReflectionUpkeep>;

export const ReflectionSummary = z.object({
  window_days: z.number().int(),
  goals: z.array(ReflectionGoal),
  upkeeps: z.array(ReflectionUpkeep),
});
export type ReflectionSummary = z.infer<typeof ReflectionSummary>;

/** A parsed capture (Q22's echo payload). */
export const ParsedCaptureInfo = z.object({
  due_date: z.string().nullable(),
  planned_date: z.string().nullable(),
  urgent: z.boolean(),
});

export const UpkeepChoice = z.object({ upkeep_id: z.number(), title: z.string() });

export const CaptureResponse = z.object({
  thought: ThoughtSummary,
  parsed: ParsedCaptureInfo,
  created_action: ActionSummary.nullable(),
  created_receipt_id: z.number().nullable(),
  receipted_upkeep: UpkeepSummary.nullable(),
  upkeep_choice: UpkeepChoice.nullable(),
});
export type CaptureResponse = z.infer<typeof CaptureResponse>;

/** The helper's one suggestion (echo-then-tap). `choice` is the pile's own
 * vocabulary; `due_date` is the ISO date STRING passed straight into an
 * action edit; `why` is a short factual reason, shown as the quiet
 * subtitle. The hand mirror of `life.api.schemas.OrganizeSuggestion`. */
export const Suggestion = z.object({
  thought_id: z.number().int(),
  choice: z.enum(["action", "upkeep", "keep", "dismiss"]),
  due_date: z.string().nullable(),
  urgent: z.boolean(),
  why: z.string(),
});
export type Suggestion = z.infer<typeof Suggestion>;

/** The organizer's whole answer. Entries never applied server-side: each
 * row is approved or rejected one by one. Mirror of
 * `life.api.schemas.OrganizeResponse`. */
export const OrganizeResponse = z.object({
  suggestions: z.array(Suggestion),
});
export type OrganizeResponse = z.infer<typeof OrganizeResponse>;

/** Finance figures the Dashboard composes (ADR 0012), read live from the
 * finance module's own endpoints — parsed with the same honesty. */
export const NetWorthPoint = z.object({
  date: z.string(),
  net_worth: z.number().int(),
});
export const SpendByCategoryPoint = z.object({
  category_id: z.number(),
  category_name: z.string(),
  kind: z.enum(["expense", "income", "transfer", "investment"]),
  amount: z.number().int(),
});
