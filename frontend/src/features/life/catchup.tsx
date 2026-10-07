/* The away strip (Q8): "you were away N days — do these still matter?"
 *
 * One strip, above the Do now panel, only when `away_days >= 2` and not yet
 * acked today. It grounds, never shames: the numbers are facts, the buttons
 * are the user's own decisions, and ignoring the strip must be allowed — so
 * the bottom line is the day's ack, and that is the ONLY persisted state
 * here.
 *
 * The per-item clearing is deliberately local: "Still matters" / "Keep"
 * remove a row from the strip's display state without touching the row —
 * matters of attention are not data. Dismiss (thought) and Rest (goal) are
 * the two HONEST mutations, and each one's server effect comes back on the
 * refetch the caller performs. The strip unmounts when no rows remain or
 * the day is acked, never before.
 */
import React from "react";
import { Link } from "react-router-dom";
import { Panel } from "@/components/primitives";
import type { CatchUpGoal, CatchUpSummary, CatchUpThought } from "./types";

const THOUGHTS_SHOWN = 8;

/** Rows surviving the local hide-set, in the summary's own order. */
const visible = <T extends { target_id: number }>(
  rows: T[],
  hidden: number[],
): T[] => rows.filter((row) => !hidden.includes(row.target_id));

/* The two row wrappers exist to give each list's shape a `target_id` the
 * `visible` helper can generic over without indexing two different id
 * spellings at the call site (goal_id / thought_id). */
export const CatchUpStrip: React.FC<{
  summary: CatchUpSummary;
  onAck: () => void;
  onRest: (goalId: number) => void;
  onDismiss: (thoughtId: number) => void;
}> = ({ summary, onAck, onRest, onDismiss }) => {
  const [hiddenGoalIds, setHiddenGoalIds] = React.useState<number[]>([]);
  const [hiddenThoughtIds, setHiddenThoughtIds] = React.useState<number[]>([]);

  /* Immutable updates everywhere — the hidden ids are display state, and a
   * screen that mutated an array in place would make a second render lie. */
  const hideGoal = (goalId: number) =>
    setHiddenGoalIds((ids) => [...ids, goalId]);
  const hideThought = (thoughtId: number) =>
    setHiddenThoughtIds((ids) => [...ids, thoughtId]);

  const goals = visible(
    summary.goals.map((goal: CatchUpGoal) => ({ ...goal, target_id: goal.goal_id })),
    hiddenGoalIds,
  );
  const thoughts = visible(
    summary.thoughts.map((thought: CatchUpThought) => ({
      ...thought,
      target_id: thought.thought_id,
    })),
    hiddenThoughtIds,
  );

  /* Q8's quiet exit: with nothing left to ask, the strip is not rendered —
   * an empty box that says "all clear" would be a nag wearing a bow. */
  if (goals.length === 0 && thoughts.length === 0) {
    return null;
  }
  const extraThoughts = thoughts.slice(THOUGHTS_SHOWN);

  const button =
    "rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] hover:bg-ground px-2.5 py-1.5 transition-colors";

  return (
    <Panel
      title="While you were away"
      description="A fact, not a score. Clear a little, and it stays light."
    >
      <p className="px-6 pt-1 text-[0.9375rem]">
        You were away {summary.away_days} days.
      </p>
      <p className="px-6 text-[0.8125rem] leading-relaxed text-ink-quiet">
        Do these still matter? Clearing a little is how it stays.
        {/* (Q8): grounded, never ashamed — no counts, no streak language. */}
      </p>

      <ul className="flex flex-col">
        {goals.map((goal) => (
          <li key={goal.goal_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-6 py-3">
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="flex items-baseline gap-2">
                <span className="text-[0.9375rem] leading-snug break-words">
                  {goal.title}
                </span>
                {goal.area !== null && (
                  <span className="eyebrow text-ink-quiet">{goal.area}</span>
                )}
              </span>
              {goal.current_focus !== null && (
                <span className="text-[0.8125rem] text-ink-quiet">
                  {goal.current_focus}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => hideGoal(goal.goal_id)}
              className={button}
            >
              Still matters
            </button>
            <button
              type="button"
              onClick={() => onRest(goal.goal_id)}
              className={button}
            >
              Rest…
            </button>
          </li>
        ))}
        {thoughts.slice(0, THOUGHTS_SHOWN).map((thought) => (
          <li key={thought.thought_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-6 py-3">
            <span className="min-w-0 flex-1 text-[0.9375rem] leading-snug break-words">
              {thought.text}
            </span>
            <button
              type="button"
              onClick={() => hideThought(thought.thought_id)}
              className={button}
            >
              Keep
            </button>
            <button
              type="button"
              onClick={() => onDismiss(thought.thought_id)}
              className={button}
            >
              Dismiss
            </button>
          </li>
        ))}
      </ul>

      {extraThoughts.length > 0 && (
        <Link
          to="/life/inbox"
          className="px-6 inline-block eyebrow text-ink-quiet hover:text-ink"
        >
          and {extraThoughts.length} more in the pile →
        </Link>
      )}

      <div className="px-6 py-4">
        <button type="button" onClick={onAck} className={button}>
          I've seen this — go quiet for today
        </button>
      </div>
    </Panel>
  );
};
