/* Upkeep — every named recurring thing, with the facts its receipts earn.
 *
 * NOTHING ON THIS PAGE CAN BE OVERDUE, AND THAT IS THE DESIGN, NOT AN
 * OMISSION. An Upkeep has an Aim (intent, never a deadline) and a last-done
 * moment; there is no due date in the model, so there is no red state to
 * render and no accumulating count to grow (ADR 0011, GLOSSARY "Upkeep").
 * The dot ages lime → cyan → a quiet fade, the line states when it was last
 * done and the aim it was given, and filing a receipt is one button.
 *
 * SETTING ONE ASIDE IS NOT DELETING IT. The row's "set aside" moves the
 * upkeep behind the "show resting" disclosure — it stops being suggested and
 * keeps every receipt it has earned, and one click brings it back. The list
 * read therefore runs twice: once for the board's own upkeeps and once for
 * the whole table, because `UpkeepSummary` carries no `is_active` and the
 * resting set is what the second read has that the first does not.
 */
import React from "react";
import { AppShell } from "@/components/AppShell";
import {
  Disclosure,
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { createUpkeep, listUpkeeps, recordReceipt, updateUpkeep } from "../api";
import type { UpkeepSummary } from "../types";
import { useFetched } from "../use-fetched";
import { UpkeepAge } from "../upkeep-age";
import { UpkeepDot } from "../upkeep-dot";

const quietButton =
  "rounded bg-ground text-ink-quiet text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 transition-colors hover:text-ink disabled:opacity-50";

const solidButton =
  "rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 transition-colors hover:bg-ground disabled:opacity-50";

const UpkeepRow: React.FC<{
  upkeep: UpkeepSummary;
  busy: boolean;
  onDone: (id: number) => void;
  onRest: (id: number) => void;
}> = ({ upkeep, busy, onDone, onRest }) => (
  <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 px-6 py-3">
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
      <UpkeepDot upkeep={upkeep} />
      <span className="text-[0.9375rem] leading-snug break-words">{upkeep.title}</span>
      <UpkeepAge upkeep={upkeep} />
    </div>
    <span className="flex shrink-0 gap-2">
      <button
        type="button"
        onClick={() => onDone(upkeep.id)}
        disabled={busy}
        className={solidButton}
      >
        Done now
      </button>
      <button
        type="button"
        onClick={() => onRest(upkeep.id)}
        disabled={busy}
        className={quietButton}
      >
        Set aside
      </button>
    </span>
  </li>
);

const RestingRow: React.FC<{
  upkeep: UpkeepSummary;
  busy: boolean;
  onWake: (id: number) => void;
}> = ({ upkeep, busy, onWake }) => (
  <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 py-2.5">
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="text-[0.875rem] leading-snug break-words text-ink-quiet">
        {upkeep.title}
      </span>
      <UpkeepAge upkeep={upkeep} />
    </span>
    <button
      type="button"
      onClick={() => onWake(upkeep.id)}
      disabled={busy}
      className={quietButton}
    >
      Wake
    </button>
  </li>
);

const NewUpkeepForm: React.FC<{ onCreated: () => void }> = ({ onCreated }) => {
  const [title, setTitle] = React.useState("");
  const [aim, setAim] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (title.trim() === "" || busy) return;
    /* The Aim is optional and it is a whole number of days: "I do this but
     * can't say how often" is a legitimate state, and a half-hearted "2ish"
     * in a number field would be a lie the board then acted on. */
    const days = aim.trim() === "" ? undefined : Number(aim.trim());
    if (days !== undefined && (!Number.isInteger(days) || days < 1)) {
      setError("An aim is a whole number of days — 7, 30, 90 — or left empty.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createUpkeep({ title: title.trim(), aim_days: days });
      setTitle("");
      setAim("");
      onCreated();
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="flex flex-col gap-3 px-6 pb-6" onSubmit={(event) => void submit(event)}>
      <Field label="a new upkeep" htmlFor="upkeep-title" error={error}>
        <input
          id="upkeep-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="change the bed sheets"
          className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
        />
      </Field>
      <Field
        label="aim (days between, optional)"
        htmlFor="upkeep-aim"
        hint="Intent, not a deadline. The earned cadence from your receipts sits beside it."
      >
        <input
          id="upkeep-aim"
          value={aim}
          inputMode="numeric"
          onChange={(event) => setAim(event.target.value)}
          placeholder="14"
          className="w-32 rounded-md bg-ground px-2.5 py-2 font-mono text-sm text-ink placeholder:text-ink-quieter"
        />
      </Field>
      <div>
        <button
          type="submit"
          disabled={title.trim() === "" || busy}
          className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-3 py-2 transition-colors hover:bg-ground disabled:opacity-50"
        >
          Name it
        </button>
      </div>
    </form>
  );
};

export const UpkeepPage: React.FC = () => {
  const upkeeps = useFetched(listUpkeeps, []);
  /* The second read is the whole table. It exists only to show what the first
   * one left out — the resting set is a difference of two lists, because the
   * summary the server sends has no `is_active` field to filter on. */
  const everyone = useFetched(() => listUpkeeps(true), []);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<number | null>(null);

  const activeIds = new Set((upkeeps.data ?? []).map((upkeep) => upkeep.id));
  const resting = (everyone.data ?? []).filter((upkeep) => !activeIds.has(upkeep.id));

  const afterMutation = React.useCallback(() => {
    upkeeps.reload();
    everyone.reload();
  }, [upkeeps, everyone]);

  const run = React.useCallback(
    async (id: number, action: () => Promise<unknown>) => {
      setBusy(id);
      setError(null);
      try {
        await action();
        afterMutation();
      } catch (cause) {
        setError(describeError(cause));
      } finally {
        setBusy(null);
      }
    },
    [afterMutation],
  );

  const onDone = React.useCallback(
    (id: number) => void run(id, () => recordReceipt(id)),
    [run],
  );
  const onRest = React.useCallback(
    (id: number) => void run(id, () => updateUpkeep(id, { is_active: false })),
    [run],
  );
  const onWake = React.useCallback(
    (id: number) => void run(id, () => updateUpkeep(id, { is_active: true })),
    [run],
  );

  return (
    <AppShell>
      <PageHeader
        title="Upkeep"
        description="The recurring things, each with when it was last done and the rhythm you aimed for. Being later than you meant is information — nothing here counts against you."
      />

      {error !== null && (
        <div className="mb-6">
          <Notice tone="error" label="the upkeep refused">
            {error}
          </Notice>
        </div>
      )}

      <div className="grid gap-8">
        <Panel title="Named upkeeps" description="Last done, and the aim it was given.">
          {upkeeps.error !== null ? (
            <Notice tone="error" label="the list didn't load">{upkeeps.error}</Notice>
          ) : upkeeps.data === null ? (
            <Skeleton label="loading upkeeps" rows={3} />
          ) : upkeeps.data.length === 0 ? (
            <EmptyState title="No named upkeeps yet.">
              Name one when a recurring thing matters enough to have a name — the
              receipts do the rest.
            </EmptyState>
          ) : (
            <ul className="flex flex-col">
              {upkeeps.data.map((upkeep) => (
                <UpkeepRow
                  key={upkeep.id}
                  upkeep={upkeep}
                  busy={busy === upkeep.id}
                  onDone={onDone}
                  onRest={onRest}
                />
              ))}
            </ul>
          )}

          {upkeeps.data !== null && resting.length > 0 && (
            <div className="px-6 pt-2 pb-5">
              <Disclosure summary="show resting">
                <p className="pt-1 pb-2">
                  Set aside, not gone: every receipt stays, and none of these are
                  suggested until they are woken.
                </p>
                <ul className="flex flex-col">
                  {resting.map((upkeep) => (
                    <RestingRow
                      key={upkeep.id}
                      upkeep={upkeep}
                      busy={busy === upkeep.id}
                      onWake={onWake}
                    />
                  ))}
                </ul>
              </Disclosure>
            </div>
          )}
        </Panel>

        <Panel title="Name one" description="A title is enough; the aim can wait until the receipts have a say.">
          <NewUpkeepForm onCreated={afterMutation} />
        </Panel>
      </div>
    </AppShell>
  );
};
