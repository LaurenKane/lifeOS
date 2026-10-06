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
 *
 * THE CATEGORY FIELD IS A PICKER, AND THE REMEMBER BOX IS OFF.
 *
 * It used to be a monospace text box holding a raw `category_id`. That asked
 * the user to know an internal identifier, which is the one thing nobody
 * standing at a transaction can do, and it made the whole ledger's spending
 * taxonomy unreadable from the screen where you correct it. `CategoryPicker`
 * replaces it with the names, grouped by kind.
 *
 * The "remember this payee" checkbox is the other half, and its default is
 * UNCHECKED, which is a correctness decision rather than a cautious one:
 *
 *   - A correction and a lesson are different acts. You fix this transaction;
 *     you do not decide what every future transaction from the same payee means.
 *   - The learned rule is stored at priority 500 and outlives the correction. It
 *     keeps firing long after this screen is closed, and it is only deleted by
 *     going to the rules page and removing it by name.
 *   - A silently-taught rule is a rule the user never saw. This product's
 *     position is that the user owns the data and overrides the machine, and a
 *     machine that teaches without asking has stopped being overridable in the
 *     one place it matters most.
 *
 * The confirmation reports the server's `learned` flag and not what was asked
 * for. Those differ: a description that reduces to no learnable pattern is
 * refused with a 422, and "remembered" printed over a lesson nobody was taught
 * is the exact failure this screen exists to avoid.
 */
import React from "react";
import { Link, useParams } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { Amount, Field, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { CategoryPicker } from "@/features/finance/categories/category-picker";
import { categoryName } from "@/features/finance/categories/category-name";
import { useCategoriesContext } from "@/features/finance/categories/use-categories";
import { useTransactionsContext, useTransaction } from "./use-transactions";
import type { DeleteOutcome } from "./use-transactions";

const COUNTER_ACCOUNT_NAME = "Expenses (system)";

/** What the last save did, in words. The four outcomes are genuinely different
 * facts about the ledger and none of them can be derived from the other.
 *
 * The remembered case names NO pattern, and that is deliberate. The stored
 * pattern is `stable_payee_pattern(raw_description)`: trailing numeric tokens
 * dropped, at most four leading tokens kept, lowercased. Computing that here
 * would be a second implementation of a server rule that can change, and it
 * would be wrong the moment it did — printing a pattern that is not the one
 * stored is worse than printing none. So the confirmation says a rule was
 * stored and links to the rules page, which is where the actual text is
 * readable. */
type SavedState =
  | { kind: "corrected"; categoryName: string }
  /* Asked to remember, and the server says it stored the lesson. */
  | { kind: "remembered"; categoryName: string }
  /* Asked to remember, and the server says it did not — with the reason, which
   * is the only part that says what to do next. */
  | { kind: "not-remembered"; categoryName: string; reason: string }
  | { kind: "empty" };

export const TransactionDetailPage: React.FC = () => {
  const params = useParams();
  const id = params.id === undefined ? null : Number.parseInt(params.id, 10);
  const { data, loading, error, reload } = useTransaction(id === null || Number.isNaN(id) ? null : id);
  const { updateTransaction, deleteTransaction } = useTransactionsContext();

  const { categories } = useCategoriesContext();
  const [categoryId, setCategoryId] = React.useState("");
  const [remember, setRemember] = React.useState(false);
  const [entryDate, setEntryDate] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<SavedState | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);
  const [outcome, setOutcome] = React.useState<DeleteOutcome | null>(null);
  const [asking, setAsking] = React.useState(false);

  const record = data;

  /* The chosen category as a CATEGORY, so every sentence below can name it
   * rather than print an id. Three states, and the difference matters:
   *
   *   - `categoryId === ""` — nothing picked. The default, and the reason the
   *     remember box is disabled.
   *   - picked, and the id is in the list — the ordinary case.
   *   - picked, and the id is NOT in the list — only reachable if the category
   *     read failed or the record names a category this build cannot see. The
   *     picker cannot produce it, so `chosen` is undefined and `chosenId` is
   *     still sent, because the record's own id is a real answer and refusing to
   *     save it would be worse than saying what it is.
   */
  const pickedId = categoryId === "" ? null : Number.parseInt(categoryId, 10);
  const chosen =
    pickedId === null || Number.isNaN(pickedId)
      ? null
      : categories.find((category) => category.id === pickedId);
  const chosenId = pickedId !== null && !Number.isNaN(pickedId) ? pickedId : undefined;

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
      const patch: { category_id?: number; entry_date?: string; learn?: boolean } = {};
      if (chosenId !== undefined) {
        patch.category_id = chosenId;
        /* `learn` travels ONLY when it was ticked AND there is a category to
         * teach. The server keys a learned rule on the record's own frozen
         * `raw_description`, so `learn` without a category is a flag attached to
         * nothing — and sending it anyway would be claiming an intention the
         * request cannot carry out. */
        if (remember) {
          patch.learn = true;
        }
      }
      if (entryDate.trim() !== "") {
        patch.entry_date = entryDate;
      }
      if (Object.keys(patch).length === 0) {
        setSaved({ kind: "empty" });
      } else {
        const updated = await updateTransaction(record.id, patch);
        const named = categoryName(chosen, chosenId ?? record.category_id ?? 0);
        /* `learn` was only sent alongside a category, so a "remembered"
           confirmation always has one to name. The guard is here because this
           function has three branches and the two that do not remember must not
           be able to print `undefined` into a sentence about the ledger. */
        /* The three outcomes, decided on the response's own `learned` flag and
         * not on what the form asked for. A description that reduces to no
         * learnable pattern comes back with `learned` false and a 422 or an
         * ignored flag, and the correction still stands — so the message says
         * the correction landed AND that nothing was taught, rather than
         * collapsing both into "saved". */
        setSaved(
          remember && updated.learned
            ? {
                kind: "remembered",
                categoryName: named,
              }
            : remember
              ? {
                  kind: "not-remembered",
                  categoryName: named,
                  reason:
                    "The ledger accepted the category but stored no rule for this description.",
                }
              : { kind: "corrected", categoryName: named },
        );
        setCategoryId("");
        setEntryDate("");
        /* The checkbox resets too, and not for tidiness: leaving it ticked means
         * the NEXT correction on this record would also teach, silently, which is
         * the behaviour this affordance exists to prevent. */
        setRemember(false);
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
                  {/* The category by NAME, with its id beside it. This row used to
                      print a bare `category_id`, which is the same defect the
                      picker was introduced to fix, in the one place a user reads
                      what a transaction currently is. The id stays: it is what the
                      ledger and every other screen agree on. */}
                  <Row term="Category">
                    {record.category_id === null ? (
                      <span className="text-money-out">none — still in the queue</span>
                    ) : (
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-medium">
                          {categoryName(
                            categories.find(
                              (candidate) => candidate.id === record.category_id,
                            ),
                            record.category_id,
                          )}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          id {record.category_id}
                        </span>
                      </span>
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
                  hint={
                    pickedId === null
                      ? record.category_id === null
                        ? "This transaction has no category, which is why it is in the queue. Choosing one here files it."
                        : "Choosing a category files this transaction under it. Leave empty to change nothing."
                      : `This transaction will be filed as ${categoryName(chosen, pickedId)}.`
                  }
                >
                  <CategoryPicker
                    id="txn-category"
                    value={categoryId}
                    onChange={setCategoryId}
                    placeholder={
                      record.category_id === null
                        ? "No category — pick one to file it"
                        : "Change the category"
                    }
                  />
                </Field>

                {/* The learn affordance. A checkbox, unchecked, with the
                    consequence in the same block as the control — not in a
                    tooltip, not on the rules page, not remembered from last time.
                    The consequence is the whole point: a learned rule outlives
                    this correction and keeps firing. */}
                <div
                  className={
                    chosen === null
                      ? "rounded-md bg-ground px-3 py-3 opacity-60"
                      : "rounded-md bg-ground px-3 py-3"
                  }
                >
                  <label
                    htmlFor="txn-learn"
                    className="flex cursor-pointer items-start gap-2.5"
                  >
                    <input
                      id="txn-learn"
                      type="checkbox"
                      checked={remember}
                      /* Disabled until a category is chosen, because a lesson
                         needs a lesson's subject: the server keys a learned rule
                         on this transaction's own description, so there is
                         nothing to teach without saying what it means. */
                      disabled={chosen === null}
                      onChange={(event) => setRemember(event.target.checked)}
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-primary"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">
                        Remember this payee
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                        {pickedId === null
                          ? "Choose a category first. There is nothing to remember until the transaction says what it is."
                          : `Future descriptions from this payee will be filed as ${categoryName(chosen, pickedId)} without asking. It is saved as a rule at priority 500, below any rule you wrote by hand, and it keeps applying until you delete it on the rules page.`}
                      </span>
                    </span>
                  </label>
                </div>

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

                {saved !== null && (
                  <Notice
                    tone={saved.kind === "not-remembered" ? "warning" : "info"}
                    label={
                      saved.kind === "remembered"
                        ? "Filed, and remembered"
                        : saved.kind === "not-remembered"
                          ? "Filed — but not remembered"
                          : saved.kind === "empty"
                            ? "Nothing to change"
                            : "Filed"
                    }
                  >
                    {saved.kind === "remembered" && (
                      <>
                        This transaction is {saved.categoryName}, and a learned rule
                        now points at it, so the next description from this payee
                        arrives already filed. The rule matches on the payee's stable
                        text, not on the order reference, so the next one can carry a
                        different number.{" "}
                        <Link
                          to="/finance/categories/rules"
                          className="underline decoration-dotted underline-offset-2"
                        >
                          See the rule
                        </Link>
                        .
                      </>
                    )}
                    {saved.kind === "not-remembered" && (
                      <>
                        This transaction is {saved.categoryName}. {saved.reason} The
                        next one from this payee will ask again.
                      </>
                    )}
                    {saved.kind === "corrected" && (
                      <>This transaction is now {saved.categoryName}. Only this one changed.</>
                    )}
                    {saved.kind === "empty" && (
                      <>Pick a category or fill in an entry date. Nothing has been sent.</>
                    )}
                  </Notice>
                )}
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


