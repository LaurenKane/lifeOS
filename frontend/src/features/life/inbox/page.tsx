/* The Inbox — the pile, processed only when the user chooses.
 *
 * Processing one Thought costs ONE decision (the choice of what it becomes);
 * why/area/minimum are refinements the goals page accepts later. "Keep" and
 * "dismiss" are both silent exits: the first returns it to rest, the second
 * stops the fourteen-day nudge — nothing is deleted from this page (Q14).
 *
 * The helper ("help me sort the pile") is an env-gated extra: suggest rows
 * only, one tap per call, applied one by one through the SAME explicit
 * resolve endpoint as the manual rows. Nothing here is ever applied without
 * a second tap; a helper that does not answer is said so plainly and the
 * pile is untouched. When the backend reports the helper unavailable, the
 * button does not exist at all.
 */
import React from "react";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { editAction, helperAvailable, listThoughts, organize, resolveThought } from "../api";
import type { Suggestion, ThoughtSummary } from "../types";
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

/** One suggestion, as words: the pile's own vocabulary, a date only when
 * the helper derived one from the user's own text, urgency only when the
 * text's own words made it so. */
const suggestionWords = (s: Suggestion): string => {
  switch (s.choice) {
    case "action": {
      const date = s.due_date === null ? "" : ` — due ${s.due_date}`;
      const urgent = s.urgent ? ", urgent" : "";
      return `become an Action${date}${urgent}`;
    }
    case "upkeep":
      return "become an Upkeep";
    case "keep":
      return "a Thought, left where it is";
    case "dismiss":
      return "not important enough to keep";
  }
};

type Pile = { thoughts: ThoughtSummary[]; helper: boolean };

export const InboxPage: React.FC = () => {
  /* One composed fetch: the pile and whether the helper exists travel
   * together, so a reload refreshes both and the counts stay one truth. */
  const pile = useFetched<Pile>(async () => {
    const [thoughts, helper] = await Promise.all([listThoughts(), helperAvailable()]);
    return { thoughts, helper };
  }, []);
  const [busy, setBusy] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [justResolved, setJustResolved] = React.useState<string | null>(null);

  /* ── The helper ────────────────────────────────────────────────────────── */
  const [asking, setAsking] = React.useState(false);
  const [helperFailed, setHelperFailed] = React.useState(false);
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([]);

  const askHelper = React.useCallback(async () => {
    setAsking(true);
    setHelperFailed(false);
    setError(null);
    try {
      const answer = await organize();
      setSuggestions(answer.suggestions);
    } catch (cause) {
      setHelperFailed(true);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAsking(false);
    }
  }, []);

  const dropSuggestion = React.useCallback((thoughtId: number) => {
    setSuggestions((rows) => rows.filter((row) => row.thought_id !== thoughtId));
  }, []);

  /* Apply goes through the SAME explicit endpoints the manual rows use:
   * the action's date/urgency is the one refinement the action edit accepts
   * afterwards. After the server's own mutation the pile refetches, and the
   * applied row drops as its thought leaves the pile. */
  const applySuggestion = React.useCallback(
    async (suggestion: Suggestion) => {
      const thought = pile.data?.thoughts.find(
        (candidate) => candidate.id === suggestion.thought_id,
      );
      if (thought === undefined) {
        dropSuggestion(suggestion.thought_id);
        return;
      }
      setBusy(suggestion.thought_id);
      setError(null);
      try {
        const resolved =
          suggestion.choice === "upkeep"
            ? await resolveThought(thought.id, { choice: "upkeep" })
            : suggestion.choice === "dismiss"
              ? await resolveThought(thought.id, { choice: "dismissed" })
              : suggestion.choice === "keep"
                ? await resolveThought(thought.id, { choice: "rests" })
                : await resolveThought(thought.id, { choice: "action" });
        const createdAction = resolved.created_action;
        if (
          suggestion.choice === "action" &&
          createdAction !== null &&
          createdAction !== undefined &&
          (suggestion.due_date !== null || suggestion.urgent)
        ) {
          await editAction(createdAction.id, {
            due_date: suggestion.due_date ?? undefined,
            urgent: suggestion.urgent,
          });
        }
        setJustResolved(thought.text);
        dropSuggestion(suggestion.thought_id);
        await pile.reload();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(null);
      }
    },
    [pile, dropSuggestion],
  );

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
        await pile.reload();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(null);
      }
    },
    [pile],
  );

  const olderThanFourteenDays = (thought: ThoughtSummary): boolean =>
    Date.now() - new Date(thought.created_at).getTime() > 14 * 86_400_000;

  const thoughts = pile.data?.thoughts ?? null;
  const helperOn = pile.data?.helper ?? false;

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

      {helperOn && thoughts !== null && thoughts.length > 0 && !asking && (
        <Panel title="A second pair of quiet eyes">
          <button
            type="button"
            onClick={() => void askHelper()}
            className="rounded bg-panel text-ink text-[0.8125rem] tracking-[0.02em] px-3 py-2 transition-colors hover:bg-ground"
          >
            Help me sort the pile
          </button>
          {helperFailed && (
            <p className="mt-3 text-[0.8125rem] leading-relaxed text-ink-quiet">
              the helper did not answer. The pile is untouched — sort by hand, or
              ask again.
            </p>
          )}
        </Panel>
      )}
      {asking && (
        <Panel title="asking the helper…">
          <p className="text-[0.8125rem] leading-relaxed text-ink-quiet">
            it either answers or doesn't — the pile is not being changed behind
            your back.
          </p>
        </Panel>
      )}

      {suggestions.length > 0 && (
        <Panel title="The helper suggests">
          <p className="px-6 pt-4 pb-0 text-[0.8125rem] leading-relaxed text-ink-quiet">
            Nothing is decided until you tap. Apply does exactly what the buttons
            below would; no thanks simply drops the row.
          </p>
          <ul className="flex flex-col">
            {suggestions.map((suggestion) => {
              const thought = thoughts?.find(
                (candidate) => candidate.id === suggestion.thought_id,
              );
              return (
                <li
                  key={suggestion.thought_id}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3"
                >
                  <span className="min-w-0 flex-1 text-[0.9375rem] leading-snug break-words">
                    {thought?.text ?? suggestion.thought_id}
                    <span className="block text-[0.8125rem] text-ink-quiet">
                      {suggestionWords(suggestion)}
                    </span>
                    {suggestion.why !== "" && (
                      <span className="block text-[0.75rem] leading-snug text-ink-quiet">
                        {suggestion.why}
                      </span>
                    )}
                  </span>
                  <span className="flex flex-wrap gap-2">
                    <ChoiceButton
                      label="apply"
                      disabled={busy === suggestion.thought_id}
                      onClick={() => void applySuggestion(suggestion)}
                    />
                    <ChoiceButton
                      label="no thanks"
                      disabled={busy === suggestion.thought_id}
                      onClick={() => dropSuggestion(suggestion.thought_id)}
                    />
                  </span>
                </li>
              );
            })}
          </ul>
        </Panel>
      )}

      <Panel title="Everything in the pile">
        {thoughts === null ? (
          pile.error !== null ? (
            <EmptyState title="The pile refused to open.">{pile.error}</EmptyState>
          ) : (
            <Skeleton label="loading the pile" rows={4} />
          )
        ) : thoughts.length === 0 ? (
          <EmptyState title="The pile is empty.">
            Either a very tidy week or a very quiet one — both fine.
          </EmptyState>
        ) : (
          <ul className="flex flex-col">
            {thoughts.map((thought) => (
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
