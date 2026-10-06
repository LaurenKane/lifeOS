/* Merchants — the names the ledger has been taught, and the raw strings that
 * stand in for them.
 *
 * THIS IS THE THIRD CURATION SCREEN AND IT IS WHERE LAYERS 2 TO 4 BECOME
 * REACHABLE. The rules screen edits what a description matches (layer 1) and the
 * categories screen edits what a transaction can be filed under. Neither could
 * reach the two tables the matcher reads for merchants, because before this
 * screen there was no way to write to them from the app: `merchant` feeds layer 3
 * by exact substring and layer 4 by fuzzy similarity, and `merchant_alias` feeds
 * layer 2. So a name a user had typed into a bank statement could not be taught
 * to the ledger without leaving the application, and layers 3 and 4 were
 * unreachable for a normal user.
 *
 * THE TWO OBJECTS ARE TWO PANELS, NOT ONE MERGED LIST.
 * `GET /merchants` and `GET /merchant-aliases` are separate routers and
 * `routes/merchants.py` explains why: an alias's merchant is optional, not its
 * parent, so nesting them would lie about the shape. Merging them into one list
 * would make an alias look like a merchant with a longer name, and the two match
 * by different rules at different layers.
 *
 * GROUPING IS BY CATEGORY KIND, THE SAME WAY THE PICKER GROUPS.
 * The category picker groups because `kind` is the classification the ledger
 * computes every total from — an expense category takes money out, an income one
 * puts it in, a transfer is neither. A user filing merchants needs that
 * distinction visible at the moment of choosing, and a flat alphabetical list
 * hides it. The two exception groups are stated on the headers, and the "Not
 * filed" one is the important one: `load_known_merchants` and `load_aliases`
 * both SKIP a row with no `category_id`, so a name on this screen that is not
 * filed is stored, read back faithfully, and matches nothing at all.
 *
 * DELETING A MERCHANT DELETES ITS ALIASES, AND THE COUNT IS STATED FIRST.
 * `merchant_alias.merchant_id` is `ON DELETE CASCADE`, so one click on a
 * merchant can remove several rows the user did not click. The confirmation
 * names how many, the same way the rules screen names rules sharing a pattern —
 * because a destructive action that is discovered after the fact is not
 * reversible. An alias's delete removes only that alias; the merchant survives. */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import {
  Disclosure,
  EmptyState,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import { useCategoriesContext } from "@/features/finance/categories/use-categories";
import { KIND_ORDER } from "@/features/finance/categories/types";
import { describeError } from "@/lib/apiClient";
import { AliasLine } from "./alias-line";
import { CuratePanel } from "./forms";
import { GroupHeader } from "./group-header";
import { MerchantLine } from "./merchant-line";
import {
  aliasesForMerchant,
  byText,
  groupByCategoryKind,
  useMerchantsContext,
} from "./use-merchants";
import type { DeleteOutcome } from "./use-merchants";

/** A row's cadence, capped — the same pair the other lists in this app use. */
const STAGGER_MS = 26;
const STAGGER_CAP = 14;
const stagger = (index: number): React.CSSProperties => ({
  animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms`,
});

/** "1 alias" and "2 aliases", in a sentence and in a button label. Spelled out
 * because a button that says "Delete 1 alias(s)" is a form that has given up. */
const plural = (count: number, noun: string): string =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;

/** What a delete is about, held so the confirmation can name it. */
type Pending =
  | { kind: "merchant"; id: number; name: string; cascade: number }
  | { kind: "alias"; id: number; rawString: string }
  | null;

type Refusal = { status: number; message: string; subject: string } | null;

export const MerchantsPage: React.FC = () => {
  const { categories, kinds, categoriesError } = useCategoriesContext();
  const {
    merchants,
    merchantsLoading,
    merchantsError,
    aliases,
    aliasesLoading,
    aliasesError,
    reloadMerchants,
    reloadAliases,
    setMerchantCategory,
    deleteMerchant,
    deleteAlias,
  } = useMerchantsContext();

  /* The category list could not be read, so every `category_id` on this screen
   * is an id with no name beside it. That degrades the grouping to two groups —
   * filed and not filed — rather than blocking the screen, because the lists
   * themselves are still perfectly readable and deleting a row does not need a
   * name. The panel says so in one line where it matters. */
  const categoriesReadable = categoriesError === null;
  const order = kinds ?? KIND_ORDER;

  const findCategory = React.useCallback(
    (id: number) => categories.find((category) => category.id === id),
    [categories],
  );

  /* Alphabetical rather than the wire order, for one reason: the merchant list
   * is read across two panels of grouped rows and a create appends, so a
   * just-created name would otherwise land at the bottom of the last group
   * instead of beside its neighbours. `localeCompare` with an explicit numeric
   * collation so "Store 2" sorts after "Store 10". */
  const sortedMerchants = React.useMemo(
    () => [...merchants].sort((a, b) => byText(a.name, b.name)),
    [merchants],
  );
  const sortedAliases = React.useMemo(
    () => [...aliases].sort((a, b) => byText(a.raw_string, b.raw_string)),
    [aliases],
  );

  const merchantGroups = React.useMemo(
    () => groupByCategoryKind(sortedMerchants, findCategory, order),
    [sortedMerchants, findCategory, order],
  );
  const aliasGroups = React.useMemo(
    () => groupByCategoryKind(sortedAliases, findCategory, order),
    [sortedAliases, findCategory, order],
  );

  const filedMerchants = merchants.filter((merchant) => merchant.category_id !== null);
  const unfiledMerchants = merchants.length - filedMerchants.length;
  const filedAliases = aliases.filter((alias) => alias.category_id !== null);

  /* One id in flight at a time, so two rows cannot both claim to be saving and a
   * second write cannot race the first one's response. */
  const [busyId, setBusyId] = React.useState<string | null>(null);
  /* The row being deleted, held so the confirmation can name it. Clicking Delete
   * does not delete: nothing has been sent, which is the only way a destructive
   * action stays honest. */
  const [pending, setPending] = React.useState<Pending>(null);
  const [refusal, setRefusal] = React.useState<Refusal>(null);
  const [announcement, setAnnouncement] = React.useState("");

  /* An error with nothing behind it is the whole panel. "No merchants" told to a
   * user with a dead backend is a statement about their ledger — and the obvious
   * next move on an empty panel is to create the merchant they were about to
   * curate, which the ledger then refuses as a duplicate. */
  const merchantsUnreadable = merchantsError !== null && merchants.length === 0;
  const aliasesUnreadable = aliasesError !== null && aliases.length === 0;

  const askToDeleteMerchant = (id: number, name: string) => {
    setRefusal(null);
    /* The cascade count is computed HERE, from the list on screen, so the
       confirmation states what is about to happen rather than finding out. */
    setPending({ kind: "merchant", id, name, cascade: aliasesForMerchant(aliases, id).length });
  };

  const askToDeleteAlias = (id: number, rawString: string) => {
    setRefusal(null);
    setPending({ kind: "alias", id, rawString });
  };

  const abandonDelete = () => {
    setPending(null);
    setAnnouncement("Delete cancelled. Nothing was removed.");
  };

  const confirmDelete = async () => {
    const target = pending;
    setPending(null);
    if (target === null) {
      return;
    }
    const key = `${target.kind}-${target.id}`;
    setBusyId(key);
    setRefusal(null);

    const outcome: DeleteOutcome =
      target.kind === "merchant"
        ? await deleteMerchant(target.id)
        : await deleteAlias(target.id);
    setBusyId(null);

    if (outcome.kind === "deleted") {
      setAnnouncement(
        target.kind === "merchant"
          ? target.cascade > 0
            ? `${target.name} and ${plural(target.cascade, "alias")} were deleted.`
            : `${target.name} was deleted.`
          : `The alias “${target.rawString}” was deleted.`,
      );
      return;
    }
    /* Nothing was removed locally in this branch, so the row is exactly where it
       was. A refusal is an answer, not a failure to report as success. */
    setRefusal({
      status: outcome.status,
      message: outcome.message,
      subject: target.kind === "merchant" ? target.name : target.rawString,
    });
    setAnnouncement(
      `Nothing was deleted for ${target.kind === "merchant" ? target.name : target.rawString}. ${outcome.message}`,
    );
  };

  const changeCategory = async (id: number, categoryId: number | null) => {
    const key = `merchant-${id}`;
    setBusyId(key);
    setRefusal(null);
    try {
      const updated = await setMerchantCategory(id, categoryId);
      const name = merchants.find((merchant) => merchant.id === id)?.name ?? `merchant ${id}`;
      setAnnouncement(
        updated.category_id === null
          ? `${name} has no category and matches nothing.`
          : `${name} is now filed as ${findCategory(updated.category_id)?.name ?? `category ${updated.category_id}`}.`,
      );
    } catch (cause) {
      /* The row keeps the value it had, because state is only ever replaced with
         what the server returned. The message goes here rather than in a page
         notice because it is about one row, and a notice six panels up would not
         be read as an answer to a picker the user just moved. */
      const message = describeError(cause);
      setRefusal({ status: 0, message, subject: "That merchant" });
      setAnnouncement(`The category was not changed. ${message}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell>
      <PageHeader
        title="Merchants"
        description={
          <>
            The names this ledger has been taught to recognise, and the raw strings
            banks send instead of them. A merchant you file here is matched on its
            own name and on close spellings of it; an alias is matched on the
            statement text itself. Both need a category before they match anything.
          </>
        }
        actions={
          /* The two halves of one subject, side by side, the same way the
             categories and rules screens offer each other. A link rather than a
             toggle because they are two addresses, not two states of one
             control. */
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/finance/categories/rules" className="btn btn-quiet">
              Categorization rules
            </Link>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                reloadMerchants();
                reloadAliases();
              }}
              disabled={merchantsLoading || aliasesLoading}
            >
              {merchantsLoading || aliasesLoading ? "Reading…" : "Reload"}
            </button>
          </div>
        }
      />

      {/* The one live region on this page. Filing and deleting both change a list
          without a new page appearing, and the row that moved or went is not
          enough of a trace on its own. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {!categoriesReadable && (
        /* Said once, above both panels, because it is one fact about a list both
           of them read. It degrades the grouping rather than blocking the
           screen: names still display and rows can still be deleted, because a
           delete is addressed by id and does not need a category name. */
        <div className="mb-6">
          <Notice
            tone="warning"
            label="Categories could not be read, so these are grouped by filing alone"
          >
            {categoriesError}
            <div className="mt-2">
              Every merchant and alias is still listed with its category id, and
              filing a new one is closed until the list comes back.
            </div>
          </Notice>
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <div className="space-y-6">
          {refusal !== null && (
            <Notice
              tone="error"
              label={`Nothing was deleted${refusal.status > 0 ? ` (${refusal.status})` : ""}`}
            >
              {refusal.message}
              <div className="mt-2">
                {refusal.subject} is still on the list.
              </div>
            </Notice>
          )}

          {/* ── Merchants ────────────────────────────────────────────────────
              The panel title is NOT "Merchants". The page title already is, and
              two headings with the same word on one page is a document outline
              that says the same thing twice — the same reason the categories
              screen calls its panel "Every category" and the rules screen calls
              its own "The rule set". These say what the list IS rather than
              repeating what the page is. */}
          <Panel
            title="Taught names"
            description={
              merchants.length === 0 ? undefined : (
                <span className="tabular-nums">
                  <span className="font-mono">{merchants.length}</span> in total ·{" "}
                  <span className="font-mono">{filedMerchants.length}</span> filed ·{" "}
                  <span className="font-mono">{unfiledMerchants}</span> matching nothing
                </span>
              )
            }
          >
            {merchantsError !== null && (
              <div className="px-5 pt-5">
                {/* The read error sits ABOVE the list, not in place of it: a
                    re-read that fails should not throw away a readable list. */}
                <Notice tone="error" label="The merchants could not be read">
                  {merchantsError}
                  <div className="mt-3">
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={reloadMerchants}
                    >
                      Try again
                    </button>
                  </div>
                </Notice>
              </div>
            )}

            {/* The confirmation lives INSIDE the panel it is about, not in place
                of the list and not at the top of the page: the row being deleted
                stays on screen and readable while the choice is open, and
                cancelling leaves the screen exactly as it was. */}
            {pending?.kind === "merchant" && (
              <div className="px-5 pt-5">
                <Notice
                  tone="warning"
                  label={`Delete ${plural(1, "merchant")} “${pending.name}”?`}
                >
                  {pending.cascade > 0 ? (
                    <>
                      {plural(pending.cascade, "alias")} named this merchant, and
                      deleting it deletes those too — not only this row.
                    </>
                  ) : (
                    <>No alias names this merchant, so only this row is removed.</>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={confirmDelete}
                    >
                      {pending.cascade > 0
                        ? `Delete the merchant and ${plural(pending.cascade, "alias")}`
                        : "Delete the merchant"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={abandonDelete}
                    >
                      Keep {pending.cascade > 0 ? "everything" : "it"}
                    </button>
                  </div>
                </Notice>
              </div>
            )}

            {merchantsLoading && merchants.length === 0 ? (
              <Skeleton label="Loading merchants" rows={4} />
            ) : merchantsUnreadable ? null : merchants.length === 0 ? (
              <EmptyState title="No merchants yet.">
                Nothing has been taught to the ledger by name, so the fuzzy pass has
                nothing to match against. The first merchant comes from naming a payee
                you recognise.
              </EmptyState>
            ) : (
              <div>
                {merchantGroups.map((group) => (
                  <section key={group.key}>
                    <GroupHeader group={group} />
                    <ul className="divide-y divide-border">
                      {group.rows.map((merchant, index) => (
                        <MerchantLine
                          key={merchant.id}
                          merchantId={merchant.id}
                          name={merchant.name}
                          categoryId={merchant.category_id}
                          category={
                            merchant.category_id === null
                              ? undefined
                              : findCategory(merchant.category_id)
                          }
                          categories={categories}
                          busy={busyId === `merchant-${merchant.id}`}
                          onCategoryChange={changeCategory}
                          onDelete={askToDeleteMerchant}
                          style={stagger(index)}
                        />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </Panel>

          {/* ── Aliases ────────────────────────────────────────────────────── */}
          <Panel
            title="Raw strings banks send"
            description={
              aliases.length === 0 ? undefined : (
                <span className="tabular-nums">
                  <span className="font-mono">{aliases.length}</span> stored ·{" "}
                  <span className="font-mono">{filedAliases.length}</span> with a
                  category
                </span>
              )
            }
          >
            {aliasesError !== null && (
              <div className="px-5 pt-5">
                <Notice tone="error" label="The aliases could not be read">
                  {aliasesError}
                  <div className="mt-3">
                    <button type="button" className="btn btn-quiet" onClick={reloadAliases}>
                      Try again
                    </button>
                  </div>
                </Notice>
              </div>
            )}

            {pending?.kind === "alias" && (
              <div className="px-5 pt-5">
                <Notice tone="warning" label={`Delete the alias “${pending.rawString}”?`}>
                  Only this string is removed. The merchant it names stays, and any
                  other alias naming that merchant stays too.
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={confirmDelete}
                    >
                      Delete the alias
                    </button>
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={abandonDelete}
                    >
                      Keep it
                    </button>
                  </div>
                </Notice>
              </div>
            )}

            {aliasesLoading && aliases.length === 0 ? (
              <Skeleton label="Loading aliases" rows={4} />
            ) : aliasesUnreadable ? null : aliases.length === 0 ? (
              <EmptyState title="No aliases yet.">
                No raw bank string has been mapped. Every description a bank sends
                reaches the matcher as itself until one is.
              </EmptyState>
            ) : (
              <div>
                {aliasGroups.map((group) => (
                  <section key={group.key}>
                    <GroupHeader group={group} />
                    <ul className="divide-y divide-border">
                      {group.rows.map((alias, index) => (
                        <AliasLine
                          key={alias.id}
                          aliasId={alias.id}
                          rawString={alias.raw_string}
                          categoryId={alias.category_id}
                          category={
                            alias.category_id === null ? undefined : findCategory(alias.category_id)
                          }
                          merchantId={alias.merchant_id}
                          merchantName={
                            alias.merchant_id === null
                              ? null
                              : (merchants.find((merchant) => merchant.id === alias.merchant_id)
                                  ?.name ?? null)
                          }
                          confidence={alias.confidence}
                          busy={busyId === `alias-${alias.id}`}
                          onDelete={askToDeleteAlias}
                          style={stagger(index)}
                        />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </Panel>

          {/* The caveat this screen most provokes, named in the summary line
              rather than hidden behind "More": a reader filing a merchant needs to
              know what filing does and does not change. */}
          <Disclosure summary="Filing a merchant does not re-file transactions already categorised">
            A merchant or alias is read when a transaction is categorised, not
            afterwards. So adding one here decides what happens to the next
            description that matches it, and leaves every transaction that already
            carries a category exactly as it is.
            <div className="mt-2">
              A merchant with no category is stored and remembered, and matches
              nothing — the matcher skips it. That is why this screen groups the
              unfiled ones and says so on their rows rather than hiding them: a
              name you cannot see doing nothing is a name you will not fix.
            </div>
            <div className="mt-2">
              An alias carries a confidence, and it decides what happens at the
              next match. At <span className="font-mono">0.90</span> or above the
              transaction is filed without asking. Below it the same transaction
              goes to the review queue instead.
            </div>
          </Disclosure>
        </div>

        <CuratePanel
          onSaved={(message) => {
            setAnnouncement(message);
            /* A category saved on the merchant panel or the alias panel changes
               what this page can group by, so both lists are re-read rather than
               left showing a group that no longer exists. */
            reloadMerchants();
            reloadAliases();
          }}
        />
      </div>
    </AppShell>
  );
};


