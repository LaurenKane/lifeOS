/* The Inbox — the pile, processed only when the user chooses.
 *
 * Processing one Thought costs ONE decision (the choice of what it becomes);
 * why/area/minimum are refinements the goals page accepts later. "Keep" and
 * "dismiss" are both silent exits: the first returns it to rest, the second
 * stops the fourteen-day nudge — nothing is deleted from this page (Q14). */
import React from "react";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { listThoughts, resolveThought } from "../api";
import type { ThoughtSummary } from "../types";
import { useFetched } from "../use-fetched";

const ChoiceButton: React.FC<{
  label: string;
  onClick: () => void;
  disabled?: boolean;
}> = ({ label, onClick, disabled }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 transition-colors hover:bg-ground disabled:opacity-50"
  >
    {label}
  </button>
);

export const InboxPage: React.FC = () => {
  const thoughts = useFetched(listThoughts, []);
  const [busy, setBusy] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [justResolved, setJustResolved] = React.useState<string | null>(null);

  const resolve = React.useCallback(
    async (
      thought: ThoughtSummary,
      body:
        | { choice: "action" | "goal" | "rests" | "dismissed" }
        | { choice: "upkeep" },
    ) => {
      setBusy(thought.id);
      setError(null);
      try {
        await resolveThought(thought.id, body);
        setJustResolved(thought.text);
        await thoughts.reload();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(null);
      }
    },
    [thoughts],
  );

  const olderThanFourteenDays = (thought: ThoughtSummary): boolean =>
    Date.now() - new Date(thought.created_at).getTime() > 14 * 86_400_000;

  return (
    <AppShell>
      <PageHeader
        title="The pile"
        description="Everything captured that hasn't sorted itself yet. A willing resting place — leaving things here for weeks is a normal week, not a failure."
      />

      {error !== null && (
        <Notice tone="error" label="there's a refusal">
          {error}
        </Notice>
      )}
      {justResolved !== null && (
        <Notice label="resolved">
          “{justResolved}” is where you pointed it. The pile got one lighter and you
          didn't have to have a plan.
        </Notice>
      )}

      <Panel title="Everything in the pile">
        {thoughts.data === null ? (
          thoughts.error !== null ? (
            <EmptyState title="The pile refused to open.">{thoughts.error}</EmptyState>
          ) : (
            <Skeleton label="loading the pile" rows={4} />
          )
        ) : thoughts.data.length === 0 ? (
          <EmptyState title="The pile is empty.">
            Either a very tidy week or a very quiet one — both fine.
          </EmptyState>
        ) : (
          <ul className="flex flex-col">
            {thoughts.data.map((thought) => (
              <li
                key={thought.id}
                className={
                  "flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3 " +
                  (olderThanFourteenDays(thought) ? "opacity-75" : "")
                }
              >
                <span className="min-w-0 flex-1 text-[0.9375rem] leading-snug break-words">
                  {thought.text}
                </span>
                <span className="flex flex-wrap gap-2">
                  <ChoiceButton label="Action" disabled={busy === thought.id} onClick={() => void resolve(thought, { choice: "action" })} />
                  <ChoiceButton label="Goal" disabled={busy === thought.id} onClick={() => void resolve(thought, { choice: "goal" })} />
                  <ChoiceButton label="Upkeep" disabled={busy === thought.id} onClick={() => void resolve(thought, { choice: "upkeep" })} />
                  <ChoiceButton label="Keep" disabled={busy === thought.id} onClick={() => void resolve(thought, { choice: "rests" })} />
                  <ChoiceButton label="Dismiss" disabled={busy === thought.id} onClick={() => void resolve(thought, { choice: "dismissed" })} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <p className="mt-6 text-[0.8125rem] leading-relaxed text-ink-quiet">
        A Thought older than two weeks is shown slightly quieter, and the Dashboard
        nudge counts them in one line — nothing more insistent than that.
      </p>
    </AppShell>
  );
};
