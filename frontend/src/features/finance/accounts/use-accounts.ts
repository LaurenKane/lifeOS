/* Accounts hook — owns the single accounts fetch.
 *
 * A failure is kept, not swallowed. The old shape did
 * `.catch(() => setData([]))`, which renders a backend that is down as a user
 * who has no accounts — and a new user is told to create one, so the failure
 * looks like the instruction. `error` is therefore part of the state and the
 * page shows the server's own words. */
import React from "react";
import { API_PATH, apiGet, apiPost, describeError, expectSchema } from "@/lib/apiClient";
import {
  AccountListSchema,
  AccountNaturesSchema,
  AccountSummarySchema,
  AccountTypesSchema,
} from "./types";
import type {
  AccountCreateRequest,
  AccountNature,
  AccountSummary,
  AccountType,
} from "./types";

export type AccountsState = {
  accounts: AccountSummary[];
  types: AccountType[];
  natures: AccountNature[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  createAccount: (input: AccountCreateRequest) => Promise<AccountSummary>;
};

export const useAccounts = (): AccountsState => {
  const [accounts, setAccounts] = React.useState<AccountSummary[]>([]);
  const [types, setTypes] = React.useState<AccountType[]>([]);
  const [natures, setNatures] = React.useState<AccountNature[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    let current = true;

    // The three taxonomies ship with the list: a form that hardcodes the
    // account types will disagree with the server the first time it does, and
    // `account_type` is a CHECK constraint — a wrong guess is a 422.
    Promise.all([
      apiGet<unknown>(`${API_PATH}/accounts`),
      apiGet<unknown>(`${API_PATH}/accounts/types`),
      apiGet<unknown>(`${API_PATH}/accounts/natures`),
    ])
      .then(([list, typeList, natureList]) => {
        if (!current) {
          return;
        }
        setAccounts(
          expectSchema(AccountListSchema, list, "accounts").map((account) =>
            expectSchema(AccountSummarySchema, account, "an account"),
          ),
        );
        setTypes(expectSchema(AccountTypesSchema, typeList, "account types"));
        setNatures(expectSchema(AccountNaturesSchema, natureList, "account natures"));
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

  /* Retrying resets the flags HERE, in the click, rather than synchronously
   * inside the effect: the effect is for the request, not for the state the
   * request implies. */
  const reload = React.useCallback(() => {
    setError(null);
    setLoading(true);
    setAttempt((n) => n + 1);
  }, []);

  const createAccount = React.useCallback(
    async (input: AccountCreateRequest): Promise<AccountSummary> => {
      const created = await apiPost<unknown, unknown>(
        `${API_PATH}/accounts`,
        input,
      );
      const account = expectSchema(AccountSummarySchema, created, "the new account");
      // The response IS the truth about the table, so it goes in immediately
      // and the list refetches behind it — the form closes on the answer it
      // was given, not on a second guess.
      setAccounts((previous) => [...previous, account]);
      return account;
    },
    [],
  );

  return { accounts, types, natures, loading, error, reload, createAccount };
};

/* The provider owns the fetch; pages read through this context. */
export const AccountsContext = React.createContext<AccountsState | null>(null);

export const useAccountsContext = () => {
  const state = React.useContext(AccountsContext);
  if (state === null) {
    throw new Error("useAccountsContext must be used within an <AccountsProvider>");
  }
  return state;
};
