/* Transactions hook — owns the single transactions fetch and every write.
 *
 * A failure is kept, not swallowed. The old shape did
 * `.catch(() => setData([]))`, which renders a backend that is down as a user
 * with no transactions — indistinguishable from a brand-new ledger, and the
 * page then invites them to go and create one. `error` is part of the state so
 * the page can show the server's own words instead of an empty table.
 */
import React from "react";
import {
  API_PATH,
  ApiError,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  describeError,
  expectSchema,
} from "@/lib/apiClient";
import { TransactionListSchema, TransactionSummarySchema } from "./types";
import type {
  ManualTransactionRequest,
  ManualTransactionUpdate,
  TransactionSummary,
} from "./types";

export type TransactionsState = {
  data: TransactionSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  createTransaction: (
    input: ManualTransactionRequest,
  ) => Promise<TransactionSummary>;
  updateTransaction: (
    id: number,
    patch: ManualTransactionUpdate,
  ) => Promise<TransactionSummary>;
  deleteTransaction: (id: number) => Promise<DeleteOutcome>;
};

/**
 * What a delete attempt actually did.
 *
 * There is no "it worked" branch in practice: the `raw_data_immutable` trigger
 * refuses every DELETE on `source_record`, and the API surfaces the trigger's
 * own message as a 409. The type still distinguishes the two, because the row
 * must only disappear when the server says it is gone — a UI that removes it
 * on a refusal is lying about a ledger.
 */
export type DeleteOutcome =
  | { kind: "deleted"; message: string }
  | { kind: "refused"; status: number; message: string };
export const useTransactions = (): TransactionsState => {
  const [data, setData] = React.useState<TransactionSummary[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/transactions`)
      .then((payload) => {
        if (current) {
          setData(expectSchema(TransactionListSchema, payload, "transactions"));
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
  }, [attempt]);

  /* Retrying resets the flags in the click, not synchronously inside the
   * effect. The effect makes the request; the click is what starts it. */
  const reload = React.useCallback(() => {
    setError(null);
    setLoading(true);
    setAttempt((n) => n + 1);
  }, []);

  const createTransaction = React.useCallback(
    async (input: ManualTransactionRequest): Promise<TransactionSummary> => {
      const created = await apiPost<unknown, unknown>(`${API_PATH}/transactions`, input);
      const transaction = expectSchema(
        TransactionSummarySchema,
        created,
        "the new transaction",
      );
      setData((previous) => [...previous, transaction]);
      return transaction;
    },
    [],
  );

  const updateTransaction = React.useCallback(
    async (
      id: number,
      patch: ManualTransactionUpdate,
    ): Promise<TransactionSummary> => {
      const updated = await apiPatch<unknown, unknown>(
        `${API_PATH}/transactions/${id}`,
        patch,
      );
      const transaction = expectSchema(
        TransactionSummarySchema,
        updated,
        "the updated transaction",
      );
      setData((previous) => previous.map((row) => (row.id === id ? transaction : row)));
      return transaction;
    },
    [],
  );

  const deleteTransaction = React.useCallback(
    async (id: number): Promise<DeleteOutcome> => {
      try {
        await apiDelete<void>(`${API_PATH}/transactions/${id}`);
      } catch (cause) {
        // A refusal is an answer, not an exception to re-throw: it carries the
        // trigger's own words and belongs on screen next to the record it was
        // asked about.
        const status = cause instanceof ApiError ? cause.status : 0;
        return { kind: "refused", status, message: describeError(cause) };
      }
      // Only now — after the server has confirmed it — does the row leave.
      setData((previous) => previous.filter((row) => row.id !== id));
      return {
        kind: "deleted",
        message: `Transaction ${id} was removed.`,
      };
    },
    [],
  );

  return {
    data,
    loading,
    error,
    reload,
    createTransaction,
    updateTransaction,
    deleteTransaction,
  };
};

export type UncategorisedState = {
  items: TransactionSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
};

/** The queue: transactions with no category, unmatched, and negative.
 *
 * Fetched on demand rather than alongside the list, because it is a different
 * question and most visits to this page are not asking it. The rows are also in
 * `data`, so switching tabs is instant once either fetch has landed. */
export const useUncategorized = (active: boolean): UncategorisedState => {
  const [items, setItems] = React.useState<TransactionSummary[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (!active) {
      return;
    }
    let current = true;

    apiGet<unknown>(`${API_PATH}/transactions/uncategorized`)
      .then((payload) => {
        if (current) {
          setItems(expectSchema(TransactionListSchema, payload, "uncategorized queue"));
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
  }, [active, attempt]);

  const reload = React.useCallback(() => {
    setError(null);
    setLoading(true);
    setAttempt((n) => n + 1);
  }, []);

  return { items, loading, error, reload };
};

/** One transaction by id, for the detail view.
 *
 * A separate read rather than a lookup in the list: `GET /transactions/{id}` is
 * the endpoint that answers "what is this record", and a record reached by URL
 * has to work when the list has not been fetched, or when it is stale.
 */
export const useTransaction = (id: number | null): {
  data: TransactionSummary | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
} => {
  /* What the LAST settled request was about. Keyed by id so that navigating
   * from one record to another shows a loading state instead of the previous
   * record's amount for a moment, and `loading` is DERIVED from it rather than
   * set imperatively — a refetch of the same record keeps the row it already
   * has, which is the truth, and re-arms nothing. */
  const [settled, setSettled] = React.useState<{
    id: number;
    data: TransactionSummary | null;
    error: string | null;
  } | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (id === null) {
      return;
    }
    let current = true;

    apiGet<unknown>(`${API_PATH}/transactions/${id}`)
      .then((payload) => {
        if (current) {
          setSettled({
            id,
            data: expectSchema(TransactionSummarySchema, payload, "that transaction"),
            error: null,
          });
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setSettled({ id, data: null, error: describeError(cause) });
        }
      });

    return () => {
      current = false;
    };
  }, [id, attempt]);

  const reload = React.useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  const mine = settled !== null && settled.id === id ? settled : null;
  return {
    data: mine?.data ?? null,
    loading: id !== null && mine === null,
    error: mine?.error ?? null,
    reload,
  };
};

/* The provider owns the fetch; pages read through this context. */
export const TransactionsContext = React.createContext<TransactionsState | null>(null);

export const useTransactionsContext = () => {
  const state = React.useContext(TransactionsContext);
  if (state === null) {
    throw new Error(
      "useTransactionsContext must be used within a <TransactionsProvider>",
    );
  }
  return state;
};
