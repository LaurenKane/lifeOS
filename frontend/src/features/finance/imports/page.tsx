/* Imports page — the batches that exist, and the one form that adds to them.
 *
 * A bankgier has one job: get one number trusted. It does that with boxed fields,
 * one prominent answer, and a tear-off stub for the part you keep. This page is
 * the same grammar — the file and the account it belongs to are boxed fields on
 * the slip, the counts come back as the prominent answer, and the mapping a
 * multi-product statement needs is the one thing asking for a decision.
 *
 * THE REVOLUT SECTION PICKER
 * --------------------------
 * A Revolut annual statement covers more than one product, so one account per
 * upload is not enough: the adapter emits `account_id=None` for any section the
 * caller did not map, on purpose, and the backend counts those rows as failed
 * with a message that says a mapping would have attributed them. Without this
 * control the Deposit half of the statement cannot be booked at all from the
 * app. It appears for `revolut_pdf` only — the other two PDF providers read a
 * single-account file, and a second account id on one of those is a claim about
 * attribution nobody made.
 *
 * Leaving a section unmapped is allowed and is not a guess: the rows are counted
 * as failed and the reason is shown, which is strictly more honest than
 * attributing them to whatever account happened to be selected above. */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { EmptyState, Field, Notice, PageHeader, Panel, Skeleton } from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { useAccountsContext } from "../accounts/use-accounts";
import { uploadImportFile, useImportsContext } from "./use-imports";
import {
  FILE_PROVIDERS,
  REVOLUT_SECTION_LABELS,
  REVOLUT_SECTIONS,
} from "./types";
import type { FileProvider, ImportSummary, RevolutSection, SectionAccountIds } from "./types";

/** What each uploadable provider is called, and what its statement covers.
 *
 * The coverage note is the useful half: all three read a PDF, so "a PDF" tells a
 * reader nothing about which of their statements belongs in this box. The server
 * refuses a mismatch with a 400 naming both, and a multi-megabyte upload is a
 * poor way to be told the wrong box was chosen. */
const PROVIDER_LABELS: Record<FileProvider, { name: string; covers: string }> = {
  amex_pdf: { name: "American Express", covers: "an Amex card statement" },
  rabobank_pdf: { name: "Rabobank", covers: "a Rabobank account statement" },
  revolut_pdf: { name: "Revolut", covers: "a Revolut statement" },
};

export const ImportsPage: React.FC = () => {
  const { data, loading, error } = useImportsContext();

  return (
    <AppShell>
      <PageHeader
        title="Imports"
        description="Bank and card statements, brought in as raw evidence. Every row keeps the file it came from, so a statement can be read again exactly as it arrived."
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <Panel title="Import batches" description="Every statement that has been read, and what became of it.">

          {error !== null ? (
            <div className="p-5">
              <Notice tone="error" label="Imports could not be read">
                {error}
              </Notice>
            </div>
          ) : loading ? (
            <Skeleton label="Loading imports" rows={3} />
          ) : data.length === 0 ? (
            <EmptyState title="No import batches.">
              Nothing has been imported yet. The form on the right takes a statement and
              writes every row it parsed — or says, per row, why it could not.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-border">
              {data.map((batch, index) => (
                <li
                  key={batch.id}
                  className="rise-row px-5 py-3.5"
                  style={{ animationDelay: `${Math.min(index, 8) * 34}ms` }}
                >
                  <p className="truncate text-sm">{batch.sourceFilename}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-quiet">
                    <span className="font-mono tabular-nums">#{batch.id}</span>
                    <span className="font-mono uppercase tracking-wide">{batch.provider}</span>
                    <span aria-hidden="true">·</span>
                    <span>{batch.status}</span>
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <NewImportPanel />
      </div>
    </AppShell>
  );
};

/* ── The form ────────────────────────────────────────────────────────────── */

type FormState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "done"; summary: ImportSummary }
  | { phase: "refused"; message: string }
  | { phase: "invalid"; message: string };

const NewImportPanel: React.FC = () => {
  const { accounts, loading, error } = useAccountsContext();
  const { reload } = useImportsContext();

  const [file, setFile] = React.useState<File | null>(null);
  const [provider, setProvider] = React.useState<FileProvider>("amex_pdf");
  const [accountId, setAccountId] = React.useState("");
  /* The mapping is held as `section -> account id`, both as strings because
   * that is what a `<select>` gives you, and the numeric ids are derived at
   * submit rather than kept in a second piece of state that could disagree. */
  const [mapping, setMapping] = React.useState<Record<RevolutSection, string>>({
    account: "",
    deposit: "",
  });
  const [state, setState] = React.useState<FormState>({ phase: "idle" });

  const isRevolut = provider === "revolut_pdf";
  const disabled = loading || accounts.length === 0 || state.phase === "uploading";

  /* Only sections the user actually chose. An unmapped section is left out of
   * the object rather than sent as null, because the backend rejects a non-integer
   * value and an absent key is the one shape that means "leave it alone". */
  const sectionAccountIds = (): SectionAccountIds => {
    if (!isRevolut) {
      return {};
    }
    const chosen: SectionAccountIds = {};
    for (const section of REVOLUT_SECTIONS) {
      const raw = mapping[section];
      if (raw !== "") {
        chosen[section] = Number(raw);
      }
    }
    return chosen;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (file === null) {
      setState({ phase: "invalid", message: "Choose a statement file first." });
      return;
    }

    const sections = sectionAccountIds();
    /* A section pointing at the account above is redundant rather than wrong,
     * and the redundant answer is the one that needs no thought. Collapsing it
     * keeps the wire shape minimal — and it means a user who picks the same
     * account twice is not silently sent a mapping that says something they did
     * not mean to claim. */
    const primary = accountId === "" ? null : Number(accountId);
    if (primary !== null && sections.account === primary) {
      delete sections.account;
    }

    setState({ phase: "uploading" });
    try {
      const summary = await uploadImportFile({
        file,
        provider,
        accountId: primary,
        sectionAccountIds: sections,
      });
      setState({ phase: "done", summary });
      // The file input cannot be cleared by setting state to null — the browser
      // owns its value — so the control is remounted with a fresh key instead.
      setFile(null);
      // The new batch belongs in the list on the left, not one upload later.
      reload();
    } catch (cause) {
      setState({ phase: "refused", message: describeError(cause) });
    }
  };

  return (
    <Panel
      title="New import"
      description="Uploaded, parsed, and written to the ledger in one step. Re-uploading the same file recognises every row rather than writing it twice."
    >
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {accounts.length === 0 && !loading && error === null && (
          <Notice tone="warning" label="You need an account first">
            A statement has to land somewhere. Create an account, then come back.
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
          label="Provider"
          htmlFor="import-provider"
          hint={`Which statement this is. ${PROVIDER_LABELS[provider].covers}, as a PDF. Anything else is refused before it is read.`}
        >
          <select
            id="import-provider"
            className="field"
            value={provider}
            disabled={disabled}
            onChange={(event) => setProvider(event.target.value as FileProvider)}
          >
            {FILE_PROVIDERS.map((value) => (
              <option key={value} value={value}>
                {PROVIDER_LABELS[value].name}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Statement file"
          htmlFor="import-file"
          hint="The PDF the bank gave you, unmodified. It is kept against the batch so a replay can read the same bytes."
        >
          <input
            key={file === null ? "empty" : file.name}
            id="import-file"
            type="file"
            accept=".pdf,application/pdf"
            className="field file:mr-3 file:rounded-md file:border-0 file:bg-ground file:px-2 file:py-1 file:text-ink file:text-xs"
            disabled={disabled}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>

        <Field
          label="Account"
          htmlFor="import-account"
          hint={
            isRevolut
              ? "Rows from the Current Account section go here when the section mapping below is left blank."
              : "The account every row in this file belongs to."
          }
        >
          <select
            id="import-account"
            className="field"
            value={accountId}
            disabled={disabled}
            onChange={(event) => setAccountId(event.target.value)}
          >
            <option value="">{accounts.length === 0 ? "No accounts yet" : "Choose an account"}</option>
            {accounts.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name} · {candidate.currency}
              </option>
            ))}
          </select>
        </Field>

        {isRevolut && (
          /* The one decision this form asks for that the others do not, so it
           * gets a fieldset with a real legend rather than a stack of selects
           * that happens to be related. The rule between it and the account
           * above is the sanctioned inside-a-panel hairline: two fields of one
           * form, not a panel edge. */
          <fieldset className="border-t border-rule pt-5">
            <legend className="eyebrow">Revolut sections</legend>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-quiet">
              An annual Revolut statement covers more than one product. Say which
              account each section belongs to. A section left blank has its rows
              counted as failed rather than booked against the wrong account.
            </p>
            <div className="mt-4 space-y-4">
              {REVOLUT_SECTIONS.map((section) => (
                <Field
                  key={section}
                  label={REVOLUT_SECTION_LABELS[section]}
                  htmlFor={`import-section-${section}`}
                >
                  <select
                    id={`import-section-${section}`}
                    className="field"
                    value={mapping[section]}
                    disabled={disabled}
                    onChange={(event) =>
                      setMapping((previous) => ({ ...previous, [section]: event.target.value }))
                    }
                  >
                    <option value="">Not mapped</option>
                    {accounts.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name} · {candidate.currency}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
            </div>
          </fieldset>
        )}

        {state.phase === "invalid" && (
          <Notice tone="error" label="Check the form">
            {state.message}
          </Notice>
        )}

        {state.phase === "refused" && (
          <Notice tone="error" label="The ledger refused this">
            {state.message}
          </Notice>
        )}

        {state.phase === "done" && <ImportOutcome summary={state.summary} />}

        <button type="submit" className="btn btn-primary w-full" disabled={disabled}>
          {state.phase === "uploading" ? "Reading the statement…" : "Import statement"}
        </button>
      </form>
    </Panel>
  );
};

/* ── The answer, and the part that did not land ──────────────────────────── */

/**
 * What came back from the import, in the order the reader needs it.
 *
 * The three counts are the headline because they are the three states a row can
 * be in, and `created + duplicated` is the question "did everything land" —
 * `status` alone is a summary word, not that answer. The failure reasons are
 * below in full and never truncated: a row refused with no stated reason is
 * indistinguishable from a row silently dropped, which is exactly the defect
 * this control exists to prevent.
 */
const ImportOutcome: React.FC<{ summary: ImportSummary }> = ({ summary }) => {
  const clean = summary.failed === 0;

  return (
    <Notice tone={clean ? "info" : "warning"} label={clean ? "Imported" : "Imported, with rows left out"}>
      <span className="font-mono tabular-nums">
        {summary.created} booked · {summary.duplicated} already there · {summary.failed} not
        booked
      </span>
      {" of "}
      <span className="font-mono tabular-nums">{summary.record_count}</span> rows the statement
      named.
      {clean ? null : (
        <span className="mt-1 block">
          Every row that was not booked says why below.
        </span>
      )}
      {summary.failures.length > 0 && (
        <details className="mt-3 border-t border-current/15 pt-2">
          <summary className="cursor-pointer text-xs font-medium">
            {summary.failures.length === 1
              ? "1 row was left out"
              : `${summary.failures.length} rows were left out`}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {summary.failures.map((reason, index) => (
              <li key={index} className="text-xs leading-relaxed">
                {reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="mt-3">
        <Link
          to="/finance/transactions"
          className="text-xs underline decoration-dotted underline-offset-2"
        >
          See the transactions
        </Link>
      </div>
    </Notice>
  );
};

