/* Transaction detail — one record, read in full; the two facts about it that
 * may change; and what happens when you ask to delete it.
 *
 * Everything shown here is the raw side, and the raw side is evidence. The
 * `raw_data_immutable` trigger freezes `raw_description` and `raw_data` and
 * refuses every DELETE, so this page does not offer an edit box for the amount,
 * the currency or the description — offering one that silently does nothing
 * would be worse than not offering it. A correction is a reversal: a new
 * balanced journal entry, which is a later milestone.
 *
 * The delete button is here on purpose rather than hidden, because the rule is
 * worth stating where the temptation is. It says what will happen before it is
 * pressed, the request is sent, and the ledger's refusal is shown in its own
 * words. The record stays exactly where it was.
 */
import React from "react";
import { Link, useParams } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Amount, Field, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { useTransactionsContext, useTransaction } from "./use-transactions";
import type { DeleteOutcome } from "./use-transactions";

const COUNTER_ACCOUNT_NAME = "Expenses (system)";

export const TransactionDetailPage: React.FC = () => {
  const params = useParams();
  const id = params.id === undefined ? null : Number.parseInt(params.id, 10);
  const { data, loading, error, reload } = useTransaction(id === null || Number.isNaN(id) ? null : id);
  const { updateTransaction, deleteTransaction } = useTransactionsContext();

  const [category, setCategory] = React.useState("");
  const [entryDate, setEntryDate] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);
  const [outcome, setOutcome] = React.useState<DeleteOutcome | null>(null);
  const [asking, setAsking] = React.useState(false);

  const record = data;

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (record === null) {
      return;
    }
    setSaving(true);
    setRefused(null);
    setSaved(null);
    try {
      // Only what the user actually touched. An absent field means "leave
      // alone" to a PATCH; sending `entry_date` unchanged would silently
      // restate a value this screen cannot read back.
      const patch: { category_id?: number; entry_date?: string } = {};
      if (category.trim() !== "") {
        const parsed = Number.parseInt(category.trim(), 10);
        if (Number.isNaN(parsed) || parsed < 1) {
          throw new Error("A category is a ledger id — a whole number of 1 or more.");
        }
        patch.category_id = parsed;
      }
      if (entryDate.trim() !== "") {
        patch.entry_date = entryDate;
      }
      if (Object.keys(patch).length === 0) {
        setSaved("Nothing to change. Fill in a category or an entry date.");
      } else {
        await updateTransaction(record.id, patch);
        setCategory("");
        setEntryDate("");
        setSaved("Saved. This is what the ledger holds now.");
        reload();
      }
    } catch (cause) {
      setRefused(describeError(cause));
    } finally {
      setSaving(false);
    }
  };

  const askToDelete = async () => {
    if (record === null) {
      return;
    }
    setAsking(true);
    setOutcome(null);
    // Awaited, and the result is shown whatever it is. The row is not removed
    // here — only `deleteTransaction` may do that, and only once the server
    // has agreed.
    setOutcome(await deleteTransaction(record.id));
    setAsking(false);
  };

  return (
    <AppShell>
      <div className="mb-6">
        <Link
          to="/finance/transactions"
          className="text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground"
        >
          ← Transactions
        </Link>
      </div>

      {id === null || Number.isNaN(id) ? (
        <Notice tone="error" label="That is not a transaction">
          A transaction is identified by a whole number.{" "}
          <Link to="/finance/transactions" className="underline underline-offset-2">
            Back to the list
          </Link>
          .
        </Notice>
      ) : error !== null ? (
        <Notice tone="error" label="This transaction could not be read">
          {error}
        </Notice>
      ) : loading || record === null ? (
        <Skeleton label="Loading transaction" rows={3} />
      ) : (
        <>
          {/* The record's id is evidence like any other field, so it is read as
              data in "The record" rather than as a kicker over the heading — a
              label above the title restated the title, and this one carried an
              id the reader may need to quote back. */}
          <PageHeader
            title={record.raw_description}
            description={
              <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <time dateTime={record.raw_date} className="font-mono text-foreground">
                  {record.raw_date}
                </time>
                <span className="font-mono">{record.status}</span>
                <span>account {record.account_id}</span>
                <Amount
                  minor={record.raw_amount}
                  currency={record.raw_currency}
                  className="text-base"
                />
              </span>
            }
          />

          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
            <div className="space-y-8">
              <Panel title="The record" description="Evidence, exactly as it was received.">
                <dl className="grid gap-x-8 gap-y-4 px-5 py-5 sm:grid-cols-2">
                  <Row term="Transaction">
                    <span className="font-mono">{record.id}</span>
                  </Row>
                  <Row term="Amount">
                    <Amount minor={record.raw_amount} currency={record.raw_currency} />
                  </Row>
                  <Row term="Currency">{record.raw_currency}</Row>
                  <Row term="Booked on">
                    <span className="font-mono">{record.raw_date}</span>
                  </Row>
                  <Row term="Status">{record.status}</Row>
                  <Row term="Account">
                    {record.account_id}
                    <Link
                      to="/finance/accounts"
                      className="ml-2 text-xs text-muted-foreground underline decoration-dotted underline-offset-2"
                    >
                      all accounts
                    </Link>
                  </Row>
                  <Row term="Category">
                    {record.category_id === null ? (
                      <span className="text-money-out">none — still in the queue</span>
                    ) : (
                      <span className="font-mono">{record.category_id}</span>
                    )}
                  </Row>
                  <Row term="Journal entry">
                    {record.journal_entry_id === null ? (
                      <span className="text-muted-foreground">not posted</span>
                    ) : (
                      <span className="font-mono">{record.journal_entry_id}</span>
                    )}
                  </Row>
                  <Row term="Transfer match">
                    {record.transfer_match_id === null ? (
                      <span className="text-muted-foreground">none</span>
                    ) : (
                      <span className="font-mono">{record.transfer_match_id}</span>
                    )}
                  </Row>
                  <Row term="Fingerprint" wide>
                    <span
                      className="block break-all font-mono text-xs leading-relaxed text-muted-foreground"
                      title="SHA-256 fingerprint. The same transaction entered twice collides on this."
                    >
                      {record.fingerprint}
                    </span>
                  </Row>
                </dl>
              </Panel>

              <Panel
                title="Corrections"
                description="The raw side is append-only. A correction is a reversal — a new, balanced journal entry — not an edit of this one."
              >
                <div className="space-y-4 p-5">
                  <button
                    type="button"
                    className="btn btn-danger"
                    onClick={askToDelete}
                    disabled={asking}
                  >
                    {asking ? "Asking the ledger…" : "Delete this transaction"}
                  </button>
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    This will not remove anything. The database refuses the delete at
                    the <span className="font-mono">raw_data_immutable</span> trigger and
                    says so; the button exists so the rule is visible where the
                    temptation is, not to make the record go away.
                  </p>

                  {outcome?.kind === "refused" && (
                    <Notice tone="warning" label={`Refused — nothing was deleted (${outcome.status})`}>
                      {outcome.message}
                    </Notice>
                  )}
                  {outcome?.kind === "deleted" && (
                    <Notice tone="info" label="Removed">
                      {outcome.message}
                    </Notice>
                  )}
                </div>
              </Panel>
            </div>

            <Panel title="Edit" description="Two fields, and only two, are ledger facts rather than evidence.">
              <form onSubmit={save} className="space-y-5 p-5" noValidate>
                <Field
                  label="Category"
                  htmlFor="txn-category"
                  hint="The id of the category this belongs to. Setting it takes the row out of the queue."
                >
                  <input
                    id="txn-category"
                    className="field font-mono"
                    inputMode="numeric"
                    value={category}
                    placeholder={record.category_id === null ? "not set" : `${record.category_id}`}
                    onChange={(event) => setCategory(event.target.value)}
                  />
                </Field>

                <Field
                  label="Entry date"
                  htmlFor="txn-entry-date"
                  hint="The accounting date on the journal entry. The API does not read it back, so this box starts empty and leaving it empty changes nothing."
                >
                  <input
                    id="txn-entry-date"
                    type="date"
                    className="field font-mono"
                    value={entryDate}
                    onChange={(event) => setEntryDate(event.target.value)}
                  />
                </Field>

                {saved !== null && <Notice tone="info" label="Saved">{saved}</Notice>}
                {refused !== null && (
                  <Notice tone="error" label="The ledger refused this">
                    {refused}
                  </Notice>
                )}

                <button type="submit" className="btn btn-primary w-full" disabled={saving}>
                  {saving ? "Saving…" : "Save changes"}
                </button>

                <p className="text-xs leading-relaxed text-muted-foreground">
                  The amount, the currency and the description are not editable. A
                  manual expense also needs its counter-leg — an{" "}
                  <span className="font-mono">equity</span> account named{" "}
                  <span className="font-mono">{COUNTER_ACCOUNT_NAME}</span> — so if
                  that account is missing the post above is refused, with the reason.
                </p>
              </form>
            </Panel>
          </div>
        </>
      )}
    </AppShell>
  );
};

const Row: React.FC<{
  term: string;
  wide?: boolean;
  children: React.ReactNode;
}> = ({ term, wide = false, children }) => (
  <div className={wide ? "sm:col-span-2" : undefined}>
    <dt className="eyebrow">{term}</dt>
    <dd className="mt-1 text-sm">{children}</dd>
  </div>
);
