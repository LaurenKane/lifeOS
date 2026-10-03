/* Transactions page — the ledger's transactions, plus the one form that adds to
 * it by hand.
 *
 * Scope is CRUD and nothing else: no charts, no budgets, no filters beyond the
 * two questions this screen is actually for ("what is recorded", "what still
 * needs a category").
 *
 * The three states that decide whether this screen is usable are all here and
 * all distinct:
 *
 *   - no accounts: a transaction cannot exist without one, so the form is
 *     present, disabled, and says why, with a link to the accounts page. It
 *     does not render as an empty ledger and invite the user to try.
 *   - the list failed to load: the server's own message, and no empty state
 *     pretending the ledger is empty. A backend that is down must not read as
 *     a clean slate.
 *   - the write was refused: the refusal is the content. A 409 from the ledger's
 *     triggers ("no counter-leg", "already recorded") is shown next to the
 *     form, in full, because the server's message is the only part that says
 *     what to do next.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Amount, EmptyState, Field, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { currencyDecimals, formatMinorUnits, parseSignedAmount, todayIso } from "@/lib/money";
import { cn } from "@/lib/utils";
import { useAccountsContext } from "../accounts/use-accounts";
import { useTransactionsContext, useUncategorized } from "./use-transactions";
import type { TransactionSummary } from "./types";

/** A row's cadence, capped so a long list arrives as a cascade rather than a
 * queue. The cap lives here, next to the constant it applies to. */
const STAGGER_MS = 34;
const STAGGER_CAP = 10;
const stagger = (index: number): React.CSSProperties => ({
  animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms`,
});

export const TransactionsPage: React.FC = () => {
  const { data, loading, error, reload } = useTransactionsContext();
  const [queueOnly, setQueueOnly] = React.useState(false);
  const queue = useUncategorized(queueOnly);

  const shown = queueOnly ? queue.items : data;
  const listError = queueOnly ? queue.error : error;
  const listLoading = queueOnly ? queue.loading : loading;
  const reloadList = queueOnly ? queue.reload : reload;

  return (
    <AppShell>
      <PageHeader
        eyebrow="Finance / Transactions"
        title="Transactions"
        description={
          <>
            Every record the ledger holds, newest last. Amounts are signed
            minor units and the sign is part of the value: <span className="font-mono">−40.50 EUR</span>{" "}
            is forty euros fifty out of the account, <span className="font-mono">+40.50 EUR</span> is
            forty euros fifty in.
          </>
        }
        actions={
          <div
            role="group"
            aria-label="Which transactions to show"
            className="flex rounded-md border border-border p-0.5"
          >
            <Segment
              active={!queueOnly}
              onClick={() => setQueueOnly(false)}
              count={data.length}
            >
              All
            </Segment>
            <Segment
              active={queueOnly}
              onClick={() => setQueueOnly(true)}
              count={queue.items.length}
            >
              Needs a category
            </Segment>
          </div>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <Panel
          title={queueOnly ? "Waiting on a category" : "All transactions"}
          description={
            queueOnly
              ? "Uncategorised, unmatched, and money out — the rows this ledger could not file for you."
              : undefined
          }
        >
          {listError !== null ? (
            <div className="p-5">
              <Notice tone="error" label="The ledger could not be read">
                {listError}
              </Notice>
              <div className="mt-4">
                <button type="button" className="btn btn-quiet" onClick={reloadList}>
                  Try again
                </button>
              </div>
            </div>
          ) : listLoading ? (
            <Skeleton label="Loading transactions" rows={5} />
          ) : shown.length === 0 ? (
            queueOnly ? (
              <EmptyState title="Nothing is waiting.">
                Every transaction the ledger holds already has a category, or is money
                coming in rather than money going out.
              </EmptyState>
            ) : (
              <EmptyState title="Nothing recorded yet.">
                The ledger is empty. Add the first entry on the right, or check the
                accounts it should belong to first.
              </EmptyState>
            )
          ) : (
            <ul className="divide-y divide-border">
              {shown.map((transaction, index) => (
                <TransactionRow
                  key={transaction.id}
                  transaction={transaction}
                  style={stagger(index)}
                />
              ))}
            </ul>
          )}
        </Panel>

        <NewTransactionPanel />
      </div>
    </AppShell>
  );
};

const Segment: React.FC<{
  active: boolean;
  onClick: () => void;
  count: number;
  children: React.ReactNode;
}> = ({ active, onClick, count, children }) => (
  <button
    type="button"
    onClick={onClick}
    aria-pressed={active}
    className={cn(
      "rounded-[0.3rem] px-2.5 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.12em] transition-colors",
      active
        ? "bg-primary text-primary-foreground"
        : "text-muted-foreground hover:text-foreground",
    )}
  >
    {children}
    <span className={cn("ml-1.5 tabular-nums", active ? "opacity-70" : "opacity-60")}>
      {count}
    </span>
  </button>
);

/* ── One row ─────────────────────────────────────────────────────────────── */

const STATUS_TONE: Record<string, string> = {
  posted: "border-money-in/30 bg-money-in/[0.07] text-money-in",
  pending: "border-border bg-muted text-muted-foreground",
  imported: "border-border bg-muted text-muted-foreground",
  duplicate: "border-destructive/30 bg-destructive/[0.07] text-destructive",
};

const TransactionRow: React.FC<{
  transaction: TransactionSummary;
  style: React.CSSProperties;
}> = ({ transaction, style }) => (
  <li className="rise" style={style}>
    <Link
      to={`/finance/transactions/${transaction.id}`}
      className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 px-5 py-3.5 transition-colors hover:bg-muted/70"
    >
      <time
        dateTime={transaction.raw_date}
        className="font-mono text-xs tabular-nums text-muted-foreground"
      >
        {transaction.raw_date}
      </time>
      <span className="min-w-0">
        <span className="block truncate text-sm">{transaction.raw_description}</span>
        <span className="mt-0.5 flex items-center gap-2">
          <span className="text-xs text-muted-foreground">account {transaction.account_id}</span>
          <span
            className={cn(
              "rounded-sm border px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-[0.1em]",
              STATUS_TONE[transaction.status] ?? STATUS_TONE.pending,
            )}
          >
            {transaction.status}
          </span>
          {transaction.category_id === null ? (
            <span className="text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-money-out/80">
              no category
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">
              {transaction.category_id}
            </span>
          )}
        </span>
      </span>
      <Amount
        minor={transaction.raw_amount}
        currency={transaction.raw_currency}
        className="text-sm"
      />
    </Link>
  </li>
);

/* ── The form ────────────────────────────────────────────────────────────── */

type FormState =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "done"; id: number; summary: string }
  | { phase: "refused"; message: string };

const NewTransactionPanel: React.FC = () => {
  const { accounts, loading, error } = useAccountsContext();
  const { createTransaction } = useTransactionsContext();

  const [accountId, setAccountId] = React.useState<string>("");
  const [description, setDescription] = React.useState("");
  const [amount, setAmount] = React.useState("");
  const [bookedDate, setBookedDate] = React.useState(todayIso);
  const [state, setState] = React.useState<FormState>({ phase: "idle" });

  const account = accounts.find((candidate) => `${candidate.id}` === accountId) ?? null;
  const currency = account?.currency ?? "EUR";

  const parsed = amount.trim() === "" ? null : parseSignedAmount(amount, currency);
  const amountError = parsed !== null && !parsed.ok ? parsed.error : null;
  const disabled = loading || accounts.length === 0 || state.phase === "saving";

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (account === null || parsed === null || !parsed.ok || description.trim() === "") {
      return;
    }
    setState({ phase: "saving" });
    try {
      const created = await createTransaction({
        account_id: account.id,
        description: description.trim(),
        amount: parsed.value,
        currency: account.currency,
        booked_date: bookedDate,
      });
      setState({
        phase: "done",
        id: created.id,
        summary: formatMinorUnits(created.raw_amount, created.raw_currency),
      });
      setDescription("");
      setAmount("");
    } catch (cause) {
      // The ledger's own words. "Refused by the ledger: … no ACTIVE account named
      // 'Expenses (system)'" is the whole answer; a generic failure message would
      // throw away the only part that says what to do next.
      setState({ phase: "refused", message: describeError(cause) });
    }
  };

  return (
    <Panel title="New transaction" description="Typed by hand. Posted to the ledger on save.">
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {accounts.length === 0 && !loading && error === null && (
          <Notice tone="warning" label="You need an account first">
            A transaction belongs to an account — the ledger will not hold one that
            belongs to nothing. Create one, then come back.
            <div className="mt-2">
              <Link to="/finance/accounts" className="btn btn-quiet">
                Go to accounts
              </Link>
            </div>
          </Notice>
        )}

        {error !== null && (
          <Notice tone="error" label="Accounts could not be read">
            {error}
          </Notice>
        )}

        <Field
          label="Account"
          htmlFor="txn-account"
          hint={
            account === null
              ? "The currency is the account's own — the ledger refuses a transaction stated in another one."
              : `Posts in ${account.currency}, the account's own currency.`
          }
        >
          <select
            id="txn-account"
            className="field"
            value={accountId}
            disabled={disabled}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">
              {accounts.length === 0 ? "No accounts yet" : "Choose an account"}
            </option>
            {accounts.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name} · {candidate.currency}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Description" htmlFor="txn-description">
          <input
            id="txn-description"
            className="field"
            value={description}
            maxLength={500}
            disabled={disabled}
            placeholder="JUMBO 4321 AMSTERDAM"
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        <Field
          label="Amount"
          htmlFor="txn-amount"
          error={amountError}
          hint={
            <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span>Money out is negative.</span>
              {parsed !== null && parsed.ok && (
                <span className="font-mono text-foreground">
                  sends “{parsed.value}” · {parsed.minor} minor units
                </span>
              )}
            </span>
          }
        >
          <input
            id="txn-amount"
            className="field font-mono"
            inputMode="decimal"
            value={amount}
            disabled={disabled}
            placeholder={currencyDecimals(currency) === 0 ? "-1200" : "-40.50"}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>

        <Field label="Booked on" htmlFor="txn-date">
          <input
            id="txn-date"
            type="date"
            className="field font-mono"
            value={bookedDate}
            disabled={disabled}
            onChange={(event) => setBookedDate(event.target.value)}
          />
        </Field>

        {state.phase === "done" && (
          <Notice tone="info" label="Posted">
            {state.summary} is on the ledger as transaction {state.id}.{" "}
            <Link
              to={`/finance/transactions/${state.id}`}
              className="underline decoration-dotted underline-offset-2"
            >
              Open it
            </Link>
            .
          </Notice>
        )}

        {state.phase === "refused" && (
          <Notice tone="error" label="The ledger refused this">
            {state.message}
          </Notice>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={disabled}>
          {state.phase === "saving" ? "Posting…" : "Post to the ledger"}
        </button>
      </form>
    </Panel>
  );
};
