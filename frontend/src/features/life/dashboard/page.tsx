/* ── The Dashboard: the app's front door ───────────────────────────────────
 *
 * What Q24 asked for, in the order it matters: the why (vision), the do-now
 * list (capped at five), urgent actions, today's upkeep opportunities, the
 * finance cards, and the pixel strip. The Inbox line is quiet by design —
 * "N things in the pile. Don't worry about these" is the sentence, never a
 * badge to open.
 *
 * NO COMPLETED-FEELING SCORES EXIST ON THIS PAGE. No streak, no percent, no
 * red (Q25, ADR 0011). The pixel strip fills on days you did anything and
 * renders an empty day as an almost invisible dot; the upkeep dot moves
 * green → amber as it ages, and amber is information, not accusation.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { amountParts, formatMinorUnits } from "@/lib/money";
import {
  ackCatchUp,
  catchUp,
  completeAction,
  listVisionItems,
  mediaUrl,
  pauseGoal,
  pixels,
  recordReceipt,
  reflection,
  resolveThought,
  todayBoard,
  updateVisionItem,
} from "../api";
import { VisionItemSummary, type ActionSummary, type ReflectionSummary } from "../types";
import { useFetched } from "../use-fetched";
import { CaptureBox } from "../capture-box";
import { CatchUpStrip } from "../catchup";
import { PixelStrip } from "../pixel-strip";
import { UpkeepAge } from "../upkeep-age";
import { UpkeepDot } from "../upkeep-dot";
import { API_PATH, apiGet, expectSchema } from "@/lib/apiClient";
import { NetWorthPoint, SpendByCategoryPoint } from "../types";

const dayOffsetIso = (days: number): string => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

const todayIso = (): string => dayOffsetIso(0);

const dueLabel = (action: ActionSummary, today: string): string | null => {
  /* "was due" — a passed real-world date is a fact, never a shaming "overdue".
   * No such state exists in the model and none appears on screen. */
  if (action.due_date !== null && action.due_date < today) {
    return "was due";
  }
  if (action.due_date === today) {
    return "due today";
  }
  return null;
};

const ActionRow: React.FC<{
  action: ActionSummary;
  onDone: (id: number) => void;
  today: string;
}> = ({ action, onDone, today }) => {
  const label = dueLabel(action, today);
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-6 py-3">
      <div className="flex min-w-0 flex-1 items-baseline gap-3">
        <span className="text-[0.9375rem] leading-snug break-words">{action.text}</span>
        {label !== null && (
          <span className="eyebrow text-ink-quiet">{label}</span>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDone(action.id)}
        className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] hover:bg-ground px-2.5 py-1.5 transition-colors"
      >
        Done
      </button>
    </li>
  );
};

export const DashboardPage: React.FC = () => {
  const board = useFetched(todayBoard, []);
  const vision = useFetched(listVisionItems, []);
  const grid = useFetched(pixels, []);
  /* The away strip (Q8): one fact "you were away N days", asked of the
   * three activity sources in one call. `acked_today` is the server state
   * that unmounts the strip for the day, so every reload never nags. */
  const catchup = useFetched(catchUp, []);
  /* The "Looking back" read: one fact-shaped month behind today's board.
   * Rendered only when something happened — both lists empty renders
   * nothing at all for it (silence is the design, not an empty state). */
  const back = useFetched(reflection, []);
  const [financeError, setFinanceError] = React.useState<string | null>(null);
  const [spendCents, setSpendCents] = React.useState<number | null>(null);
  const [netWorthCents, setNetWorthCents] = React.useState<number | null>(null);

  /* Finance composes at the app layer (ADR 0012): two reads of the finance
   * module's own endpoints, transformed here and nowhere else. A failure in
   * money does not hide the life half — it is stated as its own notice. */
  React.useEffect(() => {
    let alive = true;
    setFinanceError(null);
    (async () => {
      try {
        const spend = expectSchema(
          SpendByCategoryPoint.array(),
          await apiGet<unknown>(`${API_PATH}/analytics/spend-by-category?from=${dayOffsetIso(-30)}&to=${dayOffsetIso(0)}`),
          "spend",
        );
        const net = expectSchema(
          NetWorthPoint.array(),
          await apiGet<unknown>(`${API_PATH}/analytics/net-worth?from=${dayOffsetIso(-30)}&to=${dayOffsetIso(0)}`),
          "net worth",
        );
        if (!alive) return;
        setSpendCents(Object.values(spend).reduce((sum, point) => sum + point.amount, 0));
        setNetWorthCents(net.at(-1)?.net_worth ?? null);
      } catch (cause) {
        if (!alive) return;
        setFinanceError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const refresh = React.useCallback(() => {
    board.reload();
    grid.reload();
  }, [board, grid]);

  const onDone = React.useCallback(
    (id: number) => {
      void completeAction(id).then(refresh).catch(() => undefined);
    },
    [refresh],
  );

  const onReceipt = React.useCallback(
    (id: number) => {
      void recordReceipt(id).then(refresh).catch(() => undefined);
    },
    [refresh],
  );

  const onVisionHide = React.useCallback(
    (item: VisionItemSummary) => {
      void updateVisionItem(item.id, { is_active: false })
        .then(() => vision.reload())
        .catch(() => undefined);
    },
    [vision],
  );

  /* Strip actions. Dismissing a thought and resting a goal change facts, so
   * the reads they can affect reload; acking only changes the strip's own
   * day state, so it reloads the catch-up alone. */
  const onAck = React.useCallback(() => {
    void ackCatchUp(todayIso()).then(catchup.reload).catch(() => undefined);
  }, [catchup]);

  const onRestGoal = React.useCallback(
    (goalId: number) => {
      void pauseGoal(goalId)
        .then(() => catchup.reload())
        .catch(() => undefined);
    },
    [catchup],
  );

  const onDismissThought = React.useCallback(
    (thoughtId: number) => {
      void resolveThought(thoughtId, { choice: "dismissed" })
        .then(() => {
          catchup.reload();
          board.reload();
        })
        .catch(() => undefined);
    },
    [catchup, board],
  );

  const today = todayIso();

  return (
    <AppShell>
      <PageHeader
        title="Today"
        description="The life I'm building is worth more than the comfort of skipping this. That is the whole pitch; the rest is bookkeeping."
      />

      <div className="grid gap-8">
        <CaptureBox onCaptured={refresh} />

        {board.error !== null && (
          <Notice tone="error" label="the board didn't load">
            {board.error}
          </Notice>
        )}

        {/* The away strip (Q8), DOM-first so it reads before the board shows
         * its day-to-day face: two days of silence, one strip, no shame.
         * Rows-only render lives inside the strip — the two-line condition
         * stays here because it is the mount decision. */}
        {catchup.data !== null &&
          catchup.data.away_days >= 2 &&
          !catchup.data.acked_today && (
            <CatchUpStrip
              summary={catchup.data}
              onAck={onAck}
              onRest={onRestGoal}
              onDismiss={onDismissThought}
            />
          )}

        <div className="grid gap-8 md:grid-cols-2">
          <Panel title="Do now" description="Five top-level things. The cut is the system's; the rest wait without you re-deciding them." className="h-fit">
            {board.data === null ? (
              <Skeleton label="loading the board" rows={3} />
            ) : board.data.do_now.length === 0 && board.data.urgent.length === 0 ? (
              <EmptyState title="Nothing assigned to today — good.">
                Never nothing to want; only nothing scheduled. The pile knows what it
                heard you say, whenever you're ready.
              </EmptyState>
            ) : (
              <ul className="flex flex-col">
                {board.data.urgent.map((action) => (
                  <ActionRow key={action.id} action={action} onDone={onDone} today={today} />
                ))}
                {board.data.do_now.map((action) => (
                  <ActionRow key={action.id} action={action} onDone={onDone} today={today} />
                ))}
              </ul>
            )}
            {board.data !== null && board.data.kept_back > 0 && (
              <p className="px-6 pb-5 text-[0.8125rem] leading-relaxed text-ink-quiet">
                {board.data.kept_back} more wait — they'll surface when there's
                room, not all at once.
              </p>
            )}
          </Panel>

          <div className="grid content-start gap-8">
            <Panel title="Money" description="Live from the ledger, read-only.">
              {financeError !== null ? (
                <p className="px-6 pb-6 text-[0.8125rem] text-ink-quiet">{financeError}</p>
              ) : (
                <div className="grid grid-cols-2 items-baseline gap-4 px-6 pb-6">
                  <figure>
                    <figcaption className="eyebrow">spend · 30 days</figcaption>
                    <p className="mt-1 font-mono text-[1.5rem] tabular-nums">
                      {spendCents === null ? "—" : formatMinorUnits(spendCents, "EUR")}
                    </p>
                  </figure>
                  <figure>
                    <figcaption className="eyebrow">net worth</figcaption>
                    <p className="mt-1 font-mono text-[1.5rem] tabular-nums">
                      {netWorthCents === null
                        ? "—"
                        : amountParts(netWorthCents, "EUR").digits}
                    </p>
                  </figure>
                  <Link
                    to="/finance/overview"
                    className="eyebrow text-ink-quiet hover:text-ink"
                  >
                    open the ledger →
                  </Link>
                </div>
              )}
            </Panel>

            <Panel title="Upkeep" description="When it was last done, and the rhythm you aimed for.">
              {board.data === null ? (
                <Skeleton label="loading upkeep" rows={2} />
              ) : board.data.upkeep_opportunities.length === 0 ? (
                <EmptyState title="Nothing needs you today.">
                  The rest lives on the <Link to="/life/upkeep" className="underline">upkeep page</Link>, quiet as ever.
                </EmptyState>
              ) : (
                <ul className="flex flex-col">
                  {board.data.upkeep_opportunities.map((upkeep) => (
                    <li
                      key={upkeep.id}
                      className="flex items-baseline justify-between gap-4 px-6 py-3"
                    >
                      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
                        <UpkeepDot upkeep={upkeep} />
                        <span className="text-[0.9375rem]">{upkeep.title}</span>
                        <UpkeepAge upkeep={upkeep} />
                      </div>
                      <button
                        type="button"
                        onClick={() => onReceipt(upkeep.id)}
                        className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] hover:bg-ground px-2.5 py-1.5 transition-colors"
                      >
                        Done now
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </div>

        <div className="grid gap-8 md:grid-cols-2">
          <Panel
            title="Upcoming"
            description="Dated things on their way. No badge, no nag — a fact with a calendar day on it."
            className="h-fit"
          >
            {board.data === null ? (
              <Skeleton label="loading upcoming" rows={2} />
            ) : board.data.upcoming.length === 0 ? (
              <EmptyState title="Nothing dated in the next three days." />
            ) : (
              <ul className="flex flex-col">
                {board.data.upcoming.map((action) => (
                  <li key={action.id} className="flex items-baseline gap-4 px-6 py-3">
                    <span className="font-mono text-[0.75rem] text-ink-quiet">
                      {action.due_date}
                    </span>
                    <span className="text-[0.9375rem]">{action.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Vision" description="A few images and phrases worth waking toward.">
            {vision.data === null ? (
              <Skeleton label="loading the vision strip" rows={2} />
            ) : (
              <VisionStrip
                items={vision.data}
                onHide={onVisionHide}
              />
            )}
          </Panel>
        </div>

        {/* "Looking back" (the year's quiet reflection): rendered ONLY when
         * something happened — both lists empty means the panel does not
         * mount at all. Silence is the design; there is no empty state box
         * and no 'nothing here' message for it. */}
        {back.data !== null &&
          back.data.goals.length + back.data.upkeeps.length > 0 && (
            <Panel
              title="Looking back"
              description="The last 30 days, in what actually happened — not in points, not in a percent."
            >
              <LookingBack summary={back.data} />
            </Panel>
          )}

        <Panel title="Days you did something" description="Fills on a day you completed anything or filed a receipt. Empty days are just days.">
          {grid.data === null ? <Skeleton label="loading the grid" rows={2} /> : <PixelStrip data={grid.data} />}
        </Panel>

        <Panel
          title="The pile"
          description="Everything you captured that isn't sorted yet — worth nothing at all, for now."
        >
          {board.data === null ? (
            <Skeleton label="loading pile status" rows={1} />
          ) : (
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 px-6 pb-6">
              <p className="text-[0.9375rem]">
                {board.data.inbox.count === 0
                  ? "Nothing in the pile."
                  : `${board.data.inbox.count} thoughts in the pile. Don't worry about these.`}
              </p>
              <Link to="/life/inbox" className="eyebrow text-ink-quiet hover:text-ink">
                inbox →
              </Link>
            </div>
          )}
        </Panel>
      </div>
    </AppShell>
  );
};

/** The "Looking back" panel's body. Each goal speaks the deterministic line
 * ({N} small steps on {title}) with its done texts as quiet quotes —
 * provenance for the count, never dated headlines and never a judgment.
 * The upkeep line is the same grammar: kept going N times, a fact. */
const LookingBack: React.FC<{ summary: ReflectionSummary }> = ({ summary }) => (
  <div className="grid gap-x-8 gap-y-5 px-6 pb-6 md:grid-cols-2">
    {summary.goals.length > 0 && (
      <ul className="flex flex-col">
        {summary.goals.map((goal) => (
          <li key={goal.goal_id} className="py-2">
            <p className="text-[0.9375rem] leading-snug">
              {goal.count === 1 ? "1 small step" : `${goal.count} small steps`}
              {" on "}
              {goal.title}
            </p>
            {goal.done_texts.length > 0 && (
              <p className="mt-1 max-w-[68ch] text-[0.8125rem] leading-relaxed text-ink-quiet">
                {goal.done_texts.map((text) => `“${text}”`).join("  ·  ")}
              </p>
            )}
          </li>
        ))}
      </ul>
    )}
    {summary.upkeeps.length > 0 && (
      <ul className="flex flex-col">
        {summary.upkeeps.map((upkeep) => (
          <li
            key={upkeep.upkeep_id}
            className="py-2 text-[0.9375rem] leading-snug"
          >
            {upkeep.title} — kept going{" "}
            {upkeep.count === 1 ? "1 time" : `${upkeep.count} times`}
          </li>
        ))}
      </ul>
    )}
  </div>
);

const VisionStrip: React.FC<{
  items: VisionItemSummary[];
  onHide: (item: VisionItemSummary) => void;
}> = ({ items, onHide }) => {
  if (items.length === 0) {
    return (
      <EmptyState title="The vision board is empty.">
        Add the first phrase or image on the{" "}
        <Link to="/life/vision" className="underline">vision page</Link> — the
        why belongs at the top of the feed it appears in.
      </EmptyState>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-4 px-6 pb-6 sm:grid-cols-3">
      {items.slice(0, 6).map((item) => (
        <figure
          key={item.id}
          className="group relative overflow-hidden rounded bg-ground"
        >
          {item.kind === "image" && item.media_path !== null && (
            <img
              src={mediaUrl(item.media_path)}
              alt={item.text ?? item.area ?? "vision"}
              className="aspect-4/3 w-full object-cover"
            />
          )}
          {item.kind === "phrase" && (
            <blockquote className="aspect-4/3 flex items-center justify-center p-4 text-center text-[0.875rem] leading-snug text-ink">
              {item.text}
            </blockquote>
          )}
          <button
            type="button"
            aria-label="reframe this out of the strip"
            onClick={() => onHide(item)}
            className="absolute right-1 top-1 hidden text-white/80 hover:text-white group-hover:block"
          >
            ×
          </button>
        </figure>
      ))}
    </div>
  );
};
