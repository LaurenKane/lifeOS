/* Root — the overview page, and the 404.
 *
 * It says what this ledger can actually do right now, which is the one thing a
 * stub-era landing page is uniquely good for: the honest answer to "where do I
 * start" is two steps in a fixed order, and a first run that skips step one
 * cannot record anything at all.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel } from "@/components/primitives";
import { useAccountsContext } from "@/features/finance/accounts/use-accounts";
import { useTransactionsContext } from "@/features/finance/transactions/use-transactions";

const Step: React.FC<{
  index: string;
  title: string;
  to: string;
  cta: string;
  children: React.ReactNode;
  done: boolean;
}> = ({ index, title, to, cta, children, done }) => (
  <li className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 px-5 py-4">
    <span
      className={
        done
          ? "mt-0.5 flex size-6 items-center justify-center rounded-full bg-money-in/15 font-mono text-xs text-money-in"
          : "mt-0.5 flex size-6 items-center justify-center rounded-full border border-border font-mono text-xs text-muted-foreground"
      }
    >
      {done ? "✓" : index}
    </span>
    <div>
      <p className="text-sm">
        <Link to={to} className="font-medium underline decoration-dotted underline-offset-4">
          {title}
        </Link>
      </p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{children}</p>
      <Link to={to} className="mt-2 inline-block text-xs font-semibold uppercase tracking-[0.12em] underline decoration-dotted underline-offset-4">
        {cta}
      </Link>
    </div>
  </li>
);

export const Root: React.FC = () => {
  const { accounts, loading: accountsLoading } = useAccountsContext();
  const { data, loading: transactionsLoading } = useTransactionsContext();

  const haveAccounts = accounts.length > 0;
  const pending = accountsLoading || transactionsLoading;

  return (
    <AppShell>
      <PageHeader
        eyebrow="Life OS / Ledger"
        title="Life OS — money, double-entry."
        description="A ledger with raw evidence and balanced journal entries behind it. Records are kept, not edited: a correction is a reversal."
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <Panel title="Start here">
          <ol className="divide-y divide-border">
            <Step
              index="1"
              title="Register an account"
              to="/finance/accounts"
              cta={haveAccounts ? "Manage accounts" : "Create an account"}
              done={haveAccounts}
            >
              A transaction cannot exist without one. The nature — asset, liability
              or equity — decides which way its amounts run. A manual expense also
              needs an equity counter-leg named <span className="font-mono">Expenses (system)</span>.
            </Step>
            <Step
              index="2"
              title="Record a transaction"
              to="/finance/transactions"
              cta="Open transactions"
              done={data.length > 0}
            >
              Typed by hand: an account, a description, a signed amount and a date. It
              posts to the ledger immediately, or is refused with the reason.
            </Step>
            <Step
              index="3"
              title="File what needs filing"
              to="/finance/transactions"
              cta="Open the queue"
              done={!pending && data.length > 0 && data.every((row) => row.category_id !== null)}
            >
              Uncategorised money out waits in one queue. Setting a category is the
              only edit the ledger allows.
            </Step>
          </ol>
        </Panel>

        <Panel title="Where things stand">
          <dl className="divide-y divide-border text-sm">
            <Count term="Accounts" value={accountsLoading ? "…" : String(accounts.length)} />
            <Count
              term="Transactions"
              value={transactionsLoading ? "…" : String(data.length)}
            />
            <Count
              term="Waiting on a category"
              value={
                transactionsLoading
                  ? "…"
                  : String(data.filter((row) => row.category_id === null).length)
              }
            />
          </dl>
          <p className="px-5 py-4 text-xs leading-relaxed text-muted-foreground">
            Imports, review decisions and budgets are later milestones. Review and
            imports are not yet backed by the API; budgets does not exist as a
            concept here at all.
          </p>
        </Panel>
      </div>
    </AppShell>
  );
};

const Count: React.FC<{ term: string; value: string }> = ({ term, value }) => (
  <div className="flex items-baseline justify-between gap-4 px-5 py-3">
    <dt className="eyebrow">{term}</dt>
    <dd className="font-mono tabular-nums">{value}</dd>
  </div>
);
