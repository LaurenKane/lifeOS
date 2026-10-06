/* Review hook — owns the transfer review queue and the three decisions.
 *
 * Three changes from the stub shape, all of them because the old one lied:
 *
 *   - `.catch(() => setData([]))` turned a backend that is down into "nothing
 *     in the review queue". `error` is part of the state now.
 *   - the path was `/finance/review`, which is mounted nowhere: every router
 *     hangs off `API_V1_PREFIX` = `/api/v1`, so the read is
 *     `/v1/review/transfers`.
 *   - the item shape was invented (`description`, `amountMinor`, `status`).
 *     A queue has to be judged against the ledger, so the wire shape in
 *     `types.ts` is the ledger's: two posted lines and the matcher's score.
 *
 * THE ONE RULE ABOUT A DECISION: an item leaves the list only because someone
 * decided it, and if the server refuses, it comes back. A queue that drops a
 * row on a failed write is a queue that quietly loses work — the user's click
 * vanished and the ledger never heard about it. So `decide` removes the item
 * immediately (a queue that waits for a round trip before moving feels broken),
 * restores it at the position it was taken from when the write fails, and asks
 * the server for its list again, because the server's list is the truth rather
 * than this component's memory of it. */
import React from "react";
import {
  API_PATH,
  ApiError,
  apiGet,
  apiPost,
  describeError,
  expectSchema,
} from "@/lib/apiClient";
import {
  TransferReviewListSchema,
  TransferReviewStatsSchema,
} from "./types";
import type {
  ReviewDecision,
  TransferReviewItem,
  TransferReviewStats,
} from "./types";

/** A refused decision, kept whole so the page can name the item and the
 * status next to the server's own words. */
export type ReviewRefusal = {
  id: number;
  decision: ReviewDecision;
  status: number;
  message: string;
};

/** What a decision actually did. There is no "it worked" branch in practice —
 * the match needs the `transfer_match` table — and the type says so, because
 * the row must only stay gone when the server says it is gone. */
export type ResolveOutcome =
  | { kind: "resolved" }
  | { kind: "refused"; status: number; message: string };

export type ReviewState = {
  items: TransferReviewItem[];
  stats: TransferReviewStats | null;
  loading: boolean;
  error: string | null;
  statsError: string | null;
  refusal: ReviewRefusal | null;
  /** Ids with a decision in flight, so one row's slow write never freezes the
   * rest of the queue. */
  busy: ReadonlySet<number>;
  reload: () => void;
  decide: (
    id: number,
    decision: ReviewDecision,
    candidateJournalLineId?: number | null,
  ) => Promise<ResolveOutcome>;
};

export const useReview = (): ReviewState => {
  const [items, setItems] = React.useState<TransferReviewItem[]>([]);
  const [stats, setStats] = React.useState<TransferReviewStats | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [statsError, setStatsError] = React.useState<string | null>(null);
  const [refusal, setRefusal] = React.useState<ReviewRefusal | null>(null);
  const [busy, setBusy] = React.useState<ReadonlySet<number>>(() => new Set());
  const [request, setRequest] = React.useState({ attempt: 0, quiet: false });

  /* `items` as the last committed list, for the decision path: it has to put
   * an item back at the index it was taken from, and reading that from a state
   * setter would mean deriving the list from the caller's stale copy. */
  const itemsRef = React.useRef(items);
  React.useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/review/transfers`)
      .then((payload) => {
        if (current) {
          setItems(
            expectSchema(TransferReviewListSchema, payload, "transfer review queue"),
          );
          // A read that worked retires the last read's failure, however it was
          // asked for — including the quiet re-read after a decision, where no
          // click ever reset the banner.
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setError(describeError(cause));
        }
      })
      .finally(() => {
        if (current) {
          setLoading(false);
        }
      });

    return () => {
      current = false;
    };
  }, [request]);

  /* Queue depth is a second question with a second answer, fetched on its own
   * so that a failing count never hides the queue itself. Its failure is
   * surfaced too — quietly dropping it is how a queue that grows unnoticed
   * stops being worked through. */
  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/review/transfers/stats`)
      .then((payload) => {
        if (current) {
          setStats(expectSchema(TransferReviewStatsSchema, payload, "queue counts"));
          setStatsError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setStatsError(describeError(cause));
        }
      });

    return () => {
      current = false;
    };
  }, [request]);

  /* Retrying resets the flags in the click, not synchronously inside the
   * effect. The effect makes the request; the click is what starts it.
   *
   * A QUIET attempt — the re-read after a decision — keeps the rows already on
   * screen instead of replacing them with a skeleton: a queue that blanks and
   * reloads after every click reads as broken. */
  const reload = React.useCallback(() => {
    setError(null);
    setRefusal(null);
    setLoading(true);
    setRequest((previous) => ({ attempt: previous.attempt + 1, quiet: false }));
  }, []);

  const decide = React.useCallback(
    async (
      id: number,
      decision: ReviewDecision,
      candidateJournalLineId: number | null = null,
    ): Promise<ResolveOutcome> => {
      const snapshot = itemsRef.current;
      const index = snapshot.findIndex((item) => item.id === id);
      const removed = index === -1 ? undefined : snapshot[index];
      const message = (cause: unknown): { status: number; message: string } => ({
        status: cause instanceof ApiError ? cause.status : 0,
        message: describeError(cause),
      });

      setBusy((previous) => new Set(previous).add(id));
      setRefusal(null);
      // Optimistic: the row goes now, and the queue re-reads itself below.
      setItems((previous) => previous.filter((item) => item.id !== id));

      let outcome: ResolveOutcome;
      try {
        if (decision === "confirm") {
          // The only decision that carries a body: which candidate, if any, is
          // the other half of this movement.
          await apiPost<unknown, unknown>(
            `${API_PATH}/review/transfers/${id}/confirm`,
            { candidate_journal_line_id: candidateJournalLineId },
          );
        } else {
          /* `reject` and `ignore` take no body, and `undefined` serialises to
           * no body — which is the honest request for an endpoint that takes
           * none, rather than an invented empty object. */
          await apiPost<undefined, unknown>(
            `${API_PATH}/review/transfers/${id}/${decision}`,
            undefined,
          );
        }
        outcome = { kind: "resolved" };
      } catch (cause) {
        // Back where it was, in the same place in the queue, so the person who
        // pressed the button is looking at the same list they pressed it in.
        if (removed !== undefined) {
          setItems((previous) => {
            if (previous.some((item) => item.id === id)) {
              return previous;
            }
            const restored = [...previous];
            restored.splice(Math.min(index, restored.length), 0, removed);
            return restored;
          });
        }
        const failure = message(cause);
        setRefusal({ id, decision, ...failure });
        outcome = { kind: "refused", ...failure };
      } finally {
        setBusy((previous) => {
          const next = new Set(previous);
          next.delete(id);
          return next;
        });
      }

      // Whatever happened, the server's list is the last word: on a refusal
      // because the item may still be there, on a success because the counts
      // moved and another item may have arrived behind it.
      setRequest((previous) => ({ attempt: previous.attempt + 1, quiet: true }));
      return outcome;
    },
    [],
  );

  return {
    items,
    stats,
    loading,
    error,
    statsError,
    refusal,
    busy,
    reload,
    decide,
  };
};

/* The provider owns the fetch; pages read through this context. */
export const ReviewContext = React.createContext<ReviewState | null>(null);

export const useReviewContext = () => {
  const state = React.useContext(ReviewContext);
  if (state === null) {
    throw new Error("useReviewContext must be used within a <ReviewProvider>");
  }
  return state;
};
