/* Accounts page — the list, and the one form that adds to it.
 *
 * Accounts come first in every user's life with this ledger: a transaction
 * cannot exist without one, and a manual expense cannot post without an
 * `equity` counter-leg. So the create form is never behind a button here, and
 * an empty list is a first-run state rather than an error.
 *
 * `account_nature` is not decoration. It decides which way an amount runs, and
 * it is required with no default, so the two selects are presented as one
 * decision rather than two fields a user has to correlate.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Field, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { cn } from "@/lib/utils";
import { useAccountsContext } from "./use-accounts";
import type { AccountNature, AccountType } from "./types";

/** The three natures, spelled out. The one-liner under each is the reason the
 * field exists; without it the select is a guess. */
const NATURE_NOTES: Record<string, string> = {
  asset: "Money you hold. An asset falls when you spend from it.",
  liability: "Money you owe. Spending on a card makes the balance rise.",
  equity: "The other side of an entry. A manual expense posts against one.",
};

export const AccountsPage: React.FC = () => {
  const { accounts, loading, error, reload, createAccount } = useAccountsContext();

  const [name, setName] = React.useState("");
  const [type, setType] = React.useState<AccountType | "">("");
  const [nature, setNature] = React.useState<AccountNature | "">("");
  const [currency, setCurrency] = React.useState("EUR");
  const [saving, setSaving] = React.useState(false);
  const [created, setCreated] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);
  const [localError, setLocalError] = React.useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    setRefused(null);
    setCreated(null);

    if (name.trim() === "") {
      setLocalError("An account needs a name.");
      return;
    }
    if (type === "" || nature === "") {
      setLocalError("Choose both a type and a nature — the nature decides the arithmetic.");
      return;
    }
    if (!/^[A-Z]{3}$/.test(currency)) {
      setLocalError("Currency is a three-letter code, such as EUR.");
      return;
    }

    setSaving(true);
    try {
      const account = await createAccount({
        name: name.trim(),
        account_type: type,
        account_nature: nature,
        currency,
      });
      setName("");
      setType("");
      setNature("");
      setCreated(`${account.name} is registered as account ${account.id}.`);
    } catch (cause) {
      // A 422 here is usually the currency table: it is a row the database
      // holds, not a list the client can invent, and the server says exactly
      // which code it did not recognise.
      setRefused(describeError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell>
      <PageHeader
        title="Accounts"
        description={
          <>
            Where money sits. The nature — asset, liability or equity — is what
            decides which way an amount runs, so it is required and never guessed.
          </>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <Panel title="All accounts">
          {error !== null ? (
            <div className="p-5">
              <Notice tone="error" label="Accounts could not be read">
                {error}
              </Notice>
              <div className="mt-4">
                <button type="button" className="btn btn-quiet" onClick={reload}>
                  Try again
                </button>
              </div>
            </div>
          ) : loading ? (
            <Skeleton label="Loading accounts" rows={3} />
          ) : accounts.length === 0 ? (
            <EmptyState title="No accounts yet.">
              Nothing can be recorded against an account that does not exist. The form
              on the right is the whole of what is needed to start.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-border">
              {accounts.map((account, index) => (
                <li
                  key={account.id}
                  className="rise-row grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 px-5 py-3.5"
                  style={{ animationDelay: `${Math.min(index, 8) * 34}ms` }}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm">{account.name}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                      <span className="font-mono">#{account.id}</span>
                      <span className="font-mono uppercase tracking-wide">
                        {account.account_type}
                      </span>
                      <span aria-hidden="true">·</span>
                      <span
                        className={cn(
                          "font-semibold uppercase tracking-wide",
                          account.account_nature === "asset" && "text-money-in",
                          account.account_nature === "liability" && "text-money-out",
                          account.account_nature === "equity" && "text-foreground",
                        )}
                      >
                        {account.account_nature}
                      </span>
                    </p>
                  </div>
                  <span className="font-mono text-sm tabular-nums">{account.currency}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="New account"
          description="Registered with an id from the database. There is no edit and no delete: an account with history stays."
        >
          <form onSubmit={submit} className="space-y-5 p-5" noValidate>
            <Field label="Name" htmlFor="account-name">
              <input
                id="account-name"
                className="field"
                value={name}
                maxLength={200}
                disabled={saving}
                placeholder="Current account"
                onChange={(event) => setName(event.target.value)}
              />
            </Field>

            <Field
              label="Type"
              htmlFor="account-type"
              hint="The provider's own vocabulary. Closed list, taken from the API."
            >
              <select
                id="account-type"
                className="field"
                value={type}
                disabled={saving}
                onChange={(event) => setType(event.target.value as AccountType)}
              >
                <option value="">Choose…</option>
                <TypeOptions />
              </select>
            </Field>

            <Field
              label="Nature"
              htmlFor="account-nature"
              hint={nature === "" ? undefined : NATURE_NOTES[nature]}
            >
              <select
                id="account-nature"
                className="field"
                value={nature}
                disabled={saving}
                onChange={(event) => setNature(event.target.value as AccountNature)}
              >
                <option value="">Choose…</option>
                <NatureOptions />
              </select>
            </Field>

            <Field
              label="Currency"
              htmlFor="account-currency"
              hint="Must exist in the ledger's currency table — the server holds the decimal count that gives minor units meaning."
            >
              <input
                id="account-currency"
                className="field font-mono uppercase"
                value={currency}
                maxLength={3}
                disabled={saving}
                onChange={(event) => setCurrency(event.target.value.toUpperCase())}
              />
            </Field>

            {localError !== null && (
              <Notice tone="error" label="Check the form">
                {localError}
              </Notice>
            )}
            {refused !== null && (
              <Notice tone="error" label="The ledger refused this">
                {refused}
              </Notice>
            )}
            {created !== null && (
              <Notice tone="info" label="Registered">
                {created}{" "}
                <Link
                  to="/finance/transactions"
                  className="underline decoration-dotted underline-offset-2"
                >
                  Record something
                </Link>
                .
              </Notice>
            )}

            <button type="submit" className="btn btn-primary w-full" disabled={saving}>
              {saving ? "Registering…" : "Register account"}
            </button>
          </form>
        </Panel>
      </div>
    </AppShell>
  );
};

const TypeOptions: React.FC = () => {
  const { types } = useAccountsContext();
  return (
    <>
      {types.map((value) => (
        <option key={value} value={value}>
          {value.replace(/_/g, " ")}
        </option>
      ))}
    </>
  );
};

const NatureOptions: React.FC = () => {
  const { natures } = useAccountsContext();
  return (
    <>
      {natures.map((value) => (
        <option key={value} value={value}>
          {value}
        </option>
      ))}
    </>
  );
};
