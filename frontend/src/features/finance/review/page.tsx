/* Review queue — the transfers the matcher could not decide on its own.
 *
 * A transfer is one movement of money seen twice: once leaving an account and
 * once arriving in another. The pipeline pairs those two legs automatically
 * when it is sure, and when it is not sure it stops rather than guessing —
 * because a wrong pair is worse than no pair, since a wrong pair counts real
 * spending as a transfer and the totals stop meaning anything.
 *
 * So the job of this screen is a comparison, and the layout is built for the
 * comparison rather than for the list:
 *
 *   - ONE item is one pair of legs in one ruled block. The outbound leg is
 *     tinted and its amount set a step larger, because it is the subject; the
 *     candidates sit under it on plain paper, because they are the options.
 *   - every amount in an item shares ONE amount track, fixed rather than
 *     content-sized, so the signs and magnitudes line up on the decimal point.
 *     `−50.00` above `+50.00` is a match read before a word is read.
 *   - two facts a person would otherwise work out by eye are stated once, as
 *     arithmetic on the fields already on screen: "same amount" when the
 *     candidate is the exact negation of the outbound in the same currency, and
 *     "same account" when it is not — which is a contradiction, because
 *     money cannot leave and re-enter one own account.
 *
 * The three answers are named by the backend (`confirm`, `reject`, `ignore`)
 * because that is the vocabulary the ledger uses, and each is what a person
 * means by it: they are the same movement of money, they never were, or stop
 * asking me about this one. */
import React from "react";
import { AppShell } from "@/components/AppShell";
import {
  Amount,
  EmptyState,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import { cn } from "@/lib/utils";
import { useReviewContext } from "./use-review";
import type {
  ReviewCandidate,
  ReviewDecision,
  ReviewLeg,
  ReviewReason,
  TransferReviewItem,
  TransferReviewStats,
} from "./types";

/** A row's cadence, capped so a long queue arrives as a cascade rather than a
 * backlog. The cap lives here, next to the constant it applies to — the same
 * pair the transactions list uses. */
const STAGGER_MS = 34;
const STAGGER_CAP = 10;
const stagger = (index: number): React.CSSProperties => ({
  animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms`,
});

/** Why the item is here, in words. The wire value is the matcher's; a person
 * deciding whether two lines are one movement reads the sentence. */
const REASON_LABEL: Record<ReviewReason, string> = {
  multi_candidate: "more than one candidate",
  low_confidence: "below the confidence threshold",
};

/** The visible name of each decision, reused in the refusal notice so the
 * message names the button that was pressed. */
const DECISION_LABEL: Record<ReviewDecision, string> = {
  confirm: "Confirm",
  reject: "Reject",
  ignore: "Ignore",
};

const PAST_TENSE: Record<ReviewDecision, string> = {
  confirm: "confirmed",
  reject: "rejected",
  ignore: "ignored",
};

/* Chips: a Paper Tint fill and a Rule border at Label typography, which is the
 * system's own status chip. Tone is carried by the word as well as the colour,
 * so it survives a monochrome print. */
const CHIP =
  "rounded-sm border px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-[0.1em]";

const LEG_GRID =
  "grid grid-cols-[1.25rem_minmax(0,1fr)_6.75rem] items-start gap-x-3 px-3 py-2.5 sm:grid-cols-[1.25rem_minmax(0,1fr)_9rem] sm:gap-x-4";

const LEG_TEXT = "mt-1 block text-sm leading-snug";
const LEG_META =
  "mt-1 flex flex-wrap items-center gap-x-2 text-xs leading-relaxed text-muted-foreground";

export const ReviewPage: React.FC = () => {
  const { items, stats, loading, error, statsError, refusal, busy, reload, decide } =
    useReviewContext();
  const [announcement, setAnnouncement] = React.useState("");

  /* Every decision reports its outcome, in words, because the row it belonged
   * to is gone by the time the server answers and a queue that removes rows
   * silently leaves no trace that a click landed. */
  const answer = async (
    item: TransferReviewItem,
    decision: ReviewDecision,
    candidateJournalLineId: number | null,
  ) => {
    const outcome = await decide(item.id, decision, candidateJournalLineId);
    setAnnouncement(
      outcome.kind === "resolved"
        ? `Item ${item.id} ${PAST_TENSE[decision]}.`
        : `Item ${item.id} was not ${PAST_TENSE[decision]}: ${outcome.message}`,
    );
  };

  /* An error with nothing to show behind it is the whole screen: the queue is
   * not empty, it is unreadable, and an empty state here would be a lie. An
   * error with rows still on screen sits above them instead, because throwing
   * away a readable list over a failed re-read is its own kind of lie. */
  const unreadable = error !== null && items.length === 0;

  return (
    <AppShell>
      <PageHeader
        title="Review queue"
        description={
          <>
            Transfers the matcher could not pair on its own. <strong className="font-medium">Confirm</strong>{" "}
            records the two lines as one movement of money, so it stops counting as
            spending twice. <strong className="font-medium">Reject</strong> says they
            were never the same movement. <strong className="font-medium">Ignore</strong>{" "}
            stops the matcher suggesting this pair again.
          </>
        }
        actions={
          <button type="button" className="btn btn-quiet" onClick={reload} disabled={loading}>
            {loading ? "Reading…" : "Reload"}
          </button>
        }
      />

      {/* The only live region on the page: the queue changes without a new page
          appearing, so the one thing that needs announcing is the answer. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div className="space-y-4">
        {refusal !== null && (
          <Notice
            tone="error"
            label={`${DECISION_LABEL[refusal.decision]} was not saved${refusal.status > 0 ? ` (${refusal.status})` : ""}`}
          >
            {refusal.message}
            <div className="mt-2">Item {refusal.id} is back in the queue.</div>
          </Notice>
        )}

        {error !== null && (
          <Notice tone="error" label="The queue could not be read">
            {error}
            <div className="mt-3">
              <button type="button" className="btn btn-quiet" onClick={reload}>
                Try again
              </button>
            </div>
          </Notice>
        )}

        {statsError !== null && (
          <Notice tone="warning" label="The counts could not be read">
            {statsError}
          </Notice>
        )}

        {!unreadable && (
          <Panel
            title="Waiting on a decision"
            description={stats !== null ? <QueueDepth stats={stats} /> : undefined}
          >
            {loading && items.length === 0 ? (
              <Skeleton label="Loading the review queue" rows={3} />
            ) : items.length === 0 ? (
              <EmptyState title="Nothing in the queue.">
                No transfer is waiting on a decision. Anything the matcher cannot pair
                by itself is put here: more than one possible counterpart, or a
                match below the confidence it needs to act alone.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border">
                {items.map((item, index) => (
                  <ReviewRow
                    key={item.id}
                    item={item}
                    style={stagger(index)}
                    busy={busy.has(item.id)}
                    onAnswer={answer}
                  />
                ))}
              </ul>
            )}
          </Panel>
        )}
      </div>
    </AppShell>
  );
};

/** Queue depth, in the order the matcher would explain it: how much is
 * waiting, then why. Numbers only, no invented percentage or trend. */
const QueueDepth: React.FC<{ stats: TransferReviewStats }> = ({ stats }) => (
  <span className="tabular-nums">
    <span className="font-mono">{stats.total}</span> waiting ·{" "}
    <span className="font-mono">{stats.multi_candidate}</span> with more than one
    candidate · <span className="font-mono">{stats.low_confidence}</span> below the
    confidence threshold
  </span>
);

/* ── One item ─────────────────────────────────────────────────────────────── */

const ReviewRow: React.FC<{
  item: TransferReviewItem;
  style: React.CSSProperties;
  busy: boolean;
  onAnswer: (
    item: TransferReviewItem,
    decision: ReviewDecision,
    candidateJournalLineId: number | null,
  ) => void;
}> = ({ item, style, busy, onAnswer }) => {
  const [selected, setSelected] = React.useState<number | null>(null);

  /* More than one candidate is a question, not a yes/no, so the person names
   * the counterpart before the ledger records anything. One candidate needs no
   * ceremony: the match is either it or nothing, so Confirm states which. */
  const mustChoose = item.candidates.length > 1;
  const chosen = item.candidates.find(
    (candidate) => candidate.journal_line_id === selected,
  );
  /* An item with no candidates at all is real — the sweep found an outbound leg
   * whose other half has not been imported yet — so `null` is sent rather than
   * a guess, and the button says so. */
  const candidateJournalLineId =
    chosen?.journal_line_id ??
    (item.candidates.length === 1 ? (item.candidates[0]?.journal_line_id ?? null) : null);

  return (
    <li className="rise-row px-5 py-4" style={style}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={cn(CHIP, "border-border bg-muted text-muted-foreground")}>
          {REASON_LABEL[item.reason]}
        </span>
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          item {item.id} · queued {item.created_at}
        </span>
      </div>

      <div className="mt-3 divide-y divide-border border-y border-border">
        <OutboundLeg leg={item.outbound} />
        {item.candidates.map((candidate, index) => (
          <CandidateLeg
            key={candidate.journal_line_id}
            candidate={candidate}
            outbound={item.outbound}
            position={index + 1}
            group={`review-item-${item.id}`}
            selectable={mustChoose}
            selected={candidate.journal_line_id === selected}
            onSelect={() => setSelected(candidate.journal_line_id)}
          />
        ))}
      </div>

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || (mustChoose && chosen === undefined)}
          aria-label={`Confirm review item ${item.id}`}
          onClick={() => onAnswer(item, "confirm", candidateJournalLineId)}
        >
          Confirm
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          aria-label={`Reject review item ${item.id} as not a transfer`}
          onClick={() => onAnswer(item, "reject", null)}
        >
          Reject
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          aria-label={`Ignore review item ${item.id}`}
          onClick={() => onAnswer(item, "ignore", null)}
        >
          Ignore
        </button>

        {mustChoose && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {chosen === undefined
              ? "Choose the candidate that is the other half of this movement."
              : `Confirming pairs it with candidate ${chosen.journal_line_id}.`}
          </p>
        )}
        {!mustChoose && item.candidates.length === 0 && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            No candidate was found, so confirming clears the item without naming a
            line. Reject it if it was never a transfer.
          </p>
        )}
      </div>
    </li>
  );
};

/** The leg the queue is about: the money leaving. Tinted and a step larger,
 * because everything below it is an alternative reading of it. */
const OutboundLeg: React.FC<{ leg: ReviewLeg }> = ({ leg }) => (
  <div className={cn(LEG_GRID, "bg-muted/50")}>
    <span aria-hidden />
    <span className="min-w-0">
      <span className="eyebrow text-foreground">Outbound</span>
      <span className={LEG_TEXT}>{leg.description}</span>
      <span className={LEG_META}>
        <span>{leg.account_name}</span>
        <span aria-hidden>·</span>
        <time dateTime={leg.booked_date} className="font-mono tabular-nums">
          {leg.booked_date}
        </time>
        <span aria-hidden>·</span>
        <span className="font-mono">line {leg.journal_line_id}</span>
      </span>
    </span>
    <span className="text-right">
      <Amount minor={leg.amount_minor} currency={leg.currency} className="text-base" />
    </span>
  </div>
);

/** A candidate counterpart: the same grid as the outbound leg, so the amounts
 * share one track, and — where there is a choice to make — a radio in the first
 * track with the whole row as its target. Wrapping the radio in the label means
 * the row's own words (description, account, date, amount) are what a screen
 * reader announces for it, which are the words the decision is made on. */
const CandidateLeg: React.FC<{
  candidate: ReviewCandidate;
  outbound: ReviewLeg;
  position: number;
  group: string;
  selectable: boolean;
  selected: boolean;
  onSelect: () => void;
}> = ({ candidate, outbound, position, group, selectable, selected, onSelect }) => {
  const gap = daysBetween(outbound.booked_date, candidate.booked_date);
  const opposite =
    candidate.currency === outbound.currency &&
    candidate.amount_minor === -outbound.amount_minor;

  /* One candidate is a yes/no, so it gets no radio: a control offering a choice
   * of one offers a choice the reader did not have. The empty track keeps this
   * leg's text on the same left edge as the outbound leg's above it. */
  const Root = selectable ? "label" : "div";

  return (
    <Root
      className={cn(
        LEG_GRID,
        selectable &&
          cn(
            "cursor-pointer transition-colors",
            selected ? "bg-accent hover:bg-accent" : "hover:bg-muted/70",
          ),
      )}
    >
      {selectable ? (
        <input
          type="radio"
          name={group}
          checked={selected}
          onChange={onSelect}
          className="mt-0.5 h-3.5 w-3.5 accent-primary"
        />
      ) : (
        <span aria-hidden />
      )}
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <span className="eyebrow">Candidate {position}</span>
          {candidate.confidence.trim() !== "" && (
            <span className={cn(CHIP, "border-border bg-muted text-muted-foreground")}>
              {candidate.confidence}
            </span>
          )}
          {opposite && (
            <span className={cn(CHIP, "border-input bg-background text-foreground")}>
              same amount
            </span>
          )}
          {candidate.account_id === outbound.account_id && (
            <span className={cn(CHIP, "border-destructive/30 bg-destructive/[0.07] text-destructive")}>
              same account
            </span>
          )}
        </span>
        <span className={LEG_TEXT}>{candidate.description}</span>
        <span className={LEG_META}>
          <span>{candidate.account_name}</span>
          <span aria-hidden>·</span>
          <time dateTime={candidate.booked_date} className="font-mono tabular-nums">
            {candidate.booked_date}
          </time>
          {gap !== 0 && (
            <>
              <span aria-hidden>·</span>
              <span>
                {Math.abs(gap)} day{Math.abs(gap) === 1 ? "" : "s"} apart
              </span>
            </>
          )}
          <span aria-hidden>·</span>
          <span className="font-mono">line {candidate.journal_line_id}</span>
        </span>
      </span>
      <span className="text-right">
        <Amount minor={candidate.amount_minor} currency={candidate.currency} className="text-sm" />
      </span>
    </Root>
  );
};

/** Whole days between two `YYYY-MM-DD` calendar days, counted at UTC midnight
 * so a browser west of Greenwich cannot turn a same-day pair into two days
 * apart. Zero is not worth stating: the two dates are printed either side of
 * it. */
const daysBetween = (from: string, to: string): number => {
  const at = (value: string): number => {
    const [year = "1970", month = "1", day = "1"] = value.split("-");
    return Date.UTC(Number(year), Number(month) - 1, Number(day));
  };
  return Math.round((at(to) - at(from)) / 86_400_000);
};
