/* Goals — the why, the area, the Minimum, the Current thing. And the Mandala.
 *
 * An edit here is one field per interaction; a Goal page has no form gate, and
 * paused Goals rest in place rather than being deleted. The Mandala view
 * (discovery Q27c) is the read-only 3×3: centre = what's active, ring = the
 * eight areas with each area's goals inside. It composes from the data — a
 * map of possibilities, never a checklist — so there is nothing to maintain
 * and nothing to fail.
 */
import React from "react";
import { AppShell } from "@/components/AppShell";
import {
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import {
  activateGoal,
  createGoal,
  createWishlistItem,
  listGoals,
  listWishlist,
  pauseGoal,
  tickWishlistItem,
  updateGoal,
} from "../api";
import { AREAS, type Area, type GoalSummary } from "../types";
import { useFetched } from "../use-fetched";

const AREA_LABELS: Record<Area, { label: string; glyph: string }> = {
  home: { label: "home", glyph: "🏠" },
  hobbies: { label: "hobbies", glyph: "🎸" },
  body: { label: "body", glyph: "💪" },
  career: { label: "career", glyph: "💼" },
  money: { label: "money", glyph: "💰" },
  people: { label: "people", glyph: "👥" },
  growth: { label: "growth", glyph: "🧠" },
  experiences: { label: "experiences", glyph: "🌍" },
};

const NewGoalForm: React.FC<{ onCreated: () => void }> = ({ onCreated }) => {
  const [title, setTitle] = React.useState("");
  const [area, setArea] = React.useState<Area | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (title.trim() === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createGoal({ title: title.trim(), area: area ?? undefined });
      setTitle("");
      setArea(null);
      onCreated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="flex flex-col gap-3 px-6 pb-6" onSubmit={(e) => void submit(e)}>
      <Field label="a new goal" htmlFor="goal-title" error={error}>
        <input
          id="goal-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="become someone who plays guitar"
          className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
        />
      </Field>
      <div className="flex flex-wrap gap-1.5">
        {AREAS.map((candidate) => (
          <button
            key={candidate}
            type="button"
            onClick={() => setArea(candidate === area ? null : candidate)}
            className={
              "rounded px-2 py-1 text-[0.75rem] tracking-[0.02em] transition-colors " +
              (candidate === area
                ? "bg-lime text-ink"
                : "bg-ground text-ink-quiet hover:text-ink")
            }
          >
            {AREA_LABELS[candidate].glyph} {AREA_LABELS[candidate].label}
          </button>
        ))}
      </div>
      <div>
        <button
          type="submit"
          disabled={title.trim() === "" || busy}
          className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-3 py-2 transition-colors hover:bg-ground disabled:opacity-50"
        >
          Add goal
        </button>
      </div>
    </form>
  );
};

const GoalEdits: React.FC<{ goal: GoalSummary; onSaved: () => void }> = ({
  goal,
  onSaved,
}) => {
  const [why, setWhy] = React.useState(goal.why ?? "");
  const [minimum, setMinimum] = React.useState(goal.minimum ?? "");
  const [focus, setFocus] = React.useState(goal.current_focus ?? "");
  const [saved, setSaved] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  /* A field here sends "" for a cleared box: the backend treats the fields as
   * partial updates, so touching one field at a time is the interaction —
   * there is no Save button to press and nothing to discard. */
  const saveField = async (field: "why" | "minimum" | "current_focus") => {
    setError(null);
    try {
      const body =
        field === "why"
          ? { why }
          : field === "minimum"
            ? { minimum }
            : { current_focus: focus };
      await updateGoal(goal.id, body);
      setSaved(true);
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="flex flex-col gap-3 px-6 pb-6">
      <Field label="why" htmlFor={`why-${goal.id}`} error={error}>
        <textarea
          id={`why-${goal.id}`}
          rows={2}
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          onBlur={() => void saveField("why")}
          placeholder="why this matters — the voice that competes with comfort"
          className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="minimum (the five-minute version)"
          htmlFor={`minimum-${goal.id}`}
        >
          <input
            id={`minimum-${goal.id}`}
            value={minimum}
            onChange={(e) => setMinimum(e.target.value)}
            onBlur={() => void saveField("minimum")}
            placeholder="pick it up for five minutes"
            className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
          />
        </Field>
        <Field label="current thing" htmlFor={`focus-${goal.id}`}>
          <input
            id={`focus-${goal.id}`}
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            onBlur={() => void saveField("current_focus")}
            placeholder="learn the intro to one song"
            className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
          />
        </Field>
      </div>
      <p className="sr-only" role="status">
        {saved ? "saved" : ""}
      </p>
    </div>
  );
};

const GoalWishlist: React.FC<{ goal: GoalSummary }> = ({ goal }) => {
  const wishlist = useFetched(() => listWishlist(goal.id), [goal.id]);
  const [text, setText] = React.useState("");

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    if (text.trim() === "") return;
    await createWishlistItem(goal.id, text.trim());
    setText("");
    await wishlist.reload();
  };

  const tick = async (itemId: number, value: boolean) => {
    await tickWishlistItem(goal.id, itemId, value);
    await wishlist.reload();
  };

  return (
    <div className="px-6 pb-6">
      <p className="eyebrow">wishlist — wants, not tasks</p>
      {wishlist.data !== null && wishlist.data.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {wishlist.data.map((item) => (
            <li key={item.id} className="flex items-baseline gap-2 text-[0.8125rem]">
              <label className="flex items-baseline gap-2">
                <input
                  type="checkbox"
                  checked={item.is_done}
                  onChange={(e) => void tick(item.id, e.target.checked)}
                  className="h-3 w-3"
                />
                <span className={item.is_done ? "text-ink-quiet line-through" : ""}>
                  {item.text}
                </span>
              </label>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-[0.8125rem] text-ink-quiet">Nothing wanted yet.</p>
      )}
      <form onSubmit={(e) => void add(e)} className="mt-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="add a wanted thing (floor lamp, rug…)"
          className="flex-1 rounded-md bg-ground px-2.5 py-1.5 text-sm text-ink placeholder:text-ink-quieter"
          aria-label={`add to ${goal.title}'s wishlist`}
        />
        <button
          type="submit"
          disabled={text.trim() === ""}
          className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 hover:bg-ground disabled:opacity-50"
        >
          Add
        </button>
      </form>
    </div>
  );
};

const Mandala: React.FC<{ goals: GoalSummary[] }> = ({ goals }) => {
  /* The ring walks the 3×3 clockwise from the top-left corner — positions
   * 0,1,2 / 5,6,7 / 8,3 — and skips the centre, because the centre is not an
   * area: it is what's active, the one cell the eight areas orbit. */
  const RING_POSITIONS = [0, 1, 2, 5, 6, 7, 8, 3];

  const cellAt = (position: number): React.ReactNode => {
    if (position === 4) {
      return (
        <div key="centre" className="rounded-md bg-ground p-3">
          <p className="eyebrow">what's active</p>
          <ul className="mt-1.5 flex flex-col gap-1">
            {goals.map((goal) => (
              <li key={goal.id} className="text-[0.8125rem] leading-snug break-words">
                {goal.title}
              </li>
            ))}
            {goals.length === 0 && (
              <li className="text-[0.8125rem] leading-snug text-ink-quiet">
                Nothing active yet.
              </li>
            )}
          </ul>
        </div>
      );
    }
    const areaIndex = RING_POSITIONS.indexOf(position);
    const area = areaIndex === -1 ? undefined : AREAS[areaIndex];
    if (area === undefined) return null;
    const inside = goals.filter((goal) => goal.area === area);
    return (
      <div key={area} className="rounded-md bg-ground p-3">
        <p className="eyebrow">{AREA_LABELS[area].glyph} {AREA_LABELS[area].label}</p>
        {inside.length > 0 && (
          <ul className="mt-1.5 flex flex-col gap-1">
            {inside.map((goal) => (
              <li key={goal.id} className="text-[0.8125rem] leading-snug break-words">
                {goal.title}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  };

  return (
    <div className="grid grid-cols-3 gap-2 px-6 pb-6">
      {Array.from({ length: 9 }, (_, position) => cellAt(position))}
    </div>
  );
};

const GoalCard: React.FC<{ goal: GoalSummary; onMutated: () => void }> = ({
  goal,
  onMutated,
}) => {
  const [openEdits, setOpenEdits] = React.useState(false);

  const toggleActive = async () => {
    if (goal.is_active) {
      await pauseGoal(goal.id);
    } else {
      await activateGoal(goal.id);
    }
    onMutated();
  };

  return (
    <Panel
      title={`${goal.title}${goal.is_active ? "" : " — resting"}`}
      description={
        goal.area !== null ? (
          <>
            {AREA_LABELS[goal.area].glyph} {AREA_LABELS[goal.area].label}
          </>
        ) : undefined
      }
      className="h-fit"
    >
      <ul className="flex flex-col">
        <li className="flex flex-wrap items-baseline gap-3 px-6 pb-3">
          {goal.why !== null && (
            <p className="max-w-[68ch] text-[0.875rem] leading-relaxed text-ink">
              “{goal.why}”
            </p>
          )}
          {goal.minimum !== null && (
            <p className="text-[0.8125rem] text-ink-quiet">
              minimum: {goal.minimum}
            </p>
          )}
          {goal.current_focus !== null && (
            <p className="text-[0.8125rem] text-ink-quiet">
              current thing: <span className="text-ink">{goal.current_focus}</span>
            </p>
          )}
        </li>
      </ul>
      <div className="flex flex-wrap gap-2 px-6 pb-4">
        <button
          type="button"
          onClick={() => setOpenEdits(!openEdits)}
          className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 hover:bg-ground"
        >
          {openEdits ? "Close" : "Edit"}
        </button>
        <button
          type="button"
          onClick={() => void toggleActive()}
          className="rounded bg-ground text-ink-quiet text-[0.75rem] tracking-[0.02em] px-2.5 py-1.5 hover:text-ink"
        >
          {goal.is_active ? "Pause (rest, nothing lost)" : "Wake (back in suggestions)"}
        </button>
      </div>
      {openEdits && <GoalEdits goal={goal} onSaved={onMutated} />}
      <GoalWishlist goal={goal} />
    </Panel>
  );
};

export const GoalsPage: React.FC = () => {
  const goals = useFetched(listGoals, []);
  const active = goals.data?.filter((g) => g.is_active) ?? [];

  return (
    <AppShell>
      <PageHeader
        title="Goals"
        description="What you're becoming or finishing. The why, the five-minute minimum, and the one current thing — the rest is decoration you can skip."
      />

      {goals.error !== null && (
        <Notice tone="error" label="goals didn't load">
          {goals.error}
        </Notice>
      )}

      <Panel title="The Mandala" description="Your active goals, mapped by area. A picture of the map — never a checklist to catch up on.">
        {goals.data === null ? (
          <Skeleton label="loading the mandala" rows={3} />
        ) : (
          <Mandala goals={active} />
        )}
      </Panel>

      <div className="mt-8 grid gap-8">
        <Panel title="New goal">
          <NewGoalForm onCreated={goals.reload} />
        </Panel>

        {goals.data === null ? (
          <Skeleton label="loading goals" rows={4} />
        ) : goals.data.length === 0 ? (
          <Panel>
            <EmptyState title="No goals yet." action={undefined}>
              Say one. Titles now, why later — the page grows with you.
            </EmptyState>
          </Panel>
        ) : (
          goals.data.map((goal) => (
            <GoalCard key={goal.id} goal={goal} onMutated={goals.reload} />
          ))
        )}
      </div>
    </AppShell>
  );
};
