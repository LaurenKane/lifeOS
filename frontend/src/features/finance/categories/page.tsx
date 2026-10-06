/* Categories — what a transaction can be filed under, and the making of one more.
 *
 * WHY THIS IS A PAGE AND NOT A DRAWER ON THE RULES SCREEN.
 * A category outlives the rule that points at it and the transaction that uses
 * it, so it needs its own address: a URL a user can come back to, and a place
 * where the whole set is visible at once. The rules screen edits one thing at a
 * time; this is the inventory behind it.
 *
 * THE GROUPING IS BY KIND, NOT BY PARENT, AND THE PAGE SAYS WHY.
 * The brief asked for a tree. `CategorySummary` does not carry `parent_id` — the
 * database has the column and `POST /categories` accepts one, but no read
 * endpoint returns it — so this build cannot draw a parent/child hierarchy, and
 * it will not draw a flat list and call it a tree. What the API does report is
 * `kind`, which is the classification the ledger itself reasons about: it
 * decides a balance's sign and what a category can be budgeted as.
 *
 * So the grouping here is `kind`, and the `parent_id` field on the create form
 * says plainly that it cannot be read back. Inventing a hierarchy from an array
 * with no edges in it would be a fabrication, and on a finance screen a
 * fabricated tree is worse than an honest flat one: a user would file
 * transactions under a parent that does not exist because the UI implied it did.
 *
 * SYSTEM CATEGORIES ARE SHOWN FIRST IN THEIR GROUP AND MARKED.
 * They ship with the ledger, they are the ones a transaction can be filed
 * under, and this build has no endpoint to rename or remove them. Hiding them
 * would make the page wrong; showing them as though they were editable would
 * offer an action that does not exist. So they carry a word and no affordance. */
import React from "react";
import { Link } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import {
  Disclosure,
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import { groupByKind, useCategoriesContext } from "./use-categories";
import { KIND_LABEL, KIND_NOTE, KIND_ORDER } from "./types";
import type { Category, CategoryKind } from "./types";

/** A row's cadence, capped — the same pair the other three lists use. */
const STAGGER_MS = 26;
const STAGGER_CAP = 14;
const stagger = (index: number): React.CSSProperties => ({
  animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms`,
});

export const CategoriesPage: React.FC = () => {
  const { categories, categoriesLoading, categoriesError, reloadCategories } =
    useCategoriesContext();

  const system = categories.filter((category) => category.is_system).length;
  const yours = categories.length - system;

  /* An error with nothing behind it is the whole screen. An empty category set
     told to a user with a dead backend is a false statement, and the obvious
     next move — create the category — produces a duplicate the ledger then
     refuses. */
  const unreadable = categoriesError !== null && categories.length === 0;

  return (
    <AppShell>
      <PageHeader
        title="Categories"
        description={
          <>
            What a transaction can be filed under. Every category carries a kind, and
            the kind is what the ledger asks about: an expense category takes money out,
            an income one puts it in, and a transfer is neither — moving money between
            your own accounts is not spending.
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/finance/categories/rules" className="btn btn-quiet">
              Categorization rules
            </Link>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={reloadCategories}
              disabled={categoriesLoading}
            >
              {categoriesLoading ? "Reading…" : "Reload"}
            </button>
          </div>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <div className="space-y-6">
          {categoriesError !== null && (
            <Notice tone="error" label="The categories could not be read">
              {categoriesError}
              <div className="mt-3">
                <button type="button" className="btn btn-quiet" onClick={reloadCategories}>
                  Try again
                </button>
              </div>
            </Notice>
          )}

          {!unreadable && (
            <Panel
              title="Every category"
              description={
                categories.length === 0 ? undefined : (
                  <span className="tabular-nums">
                    <span className="font-mono">{categories.length}</span> in total ·{" "}
                    <span className="font-mono">{yours}</span> yours ·{" "}
                    <span className="font-mono">{system}</span> that ship with the ledger
                  </span>
                )
              }
            >
              {categoriesLoading && categories.length === 0 ? (
                <Skeleton label="Loading categories" rows={5} />
              ) : categories.length === 0 ? (
                <EmptyState title="No categories at all.">
                  Nothing exists to file a transaction under, not even the ones this
                  application ships with. Nothing will be categorised until a category
                  does.
                </EmptyState>
              ) : (
                <div>
                  {groupByKind(categories, KIND_ORDER).map((group) => (
                    <KindGroup key={group.kind} kind={group.kind} categories={group.categories} />
                  ))}
                </div>
              )}
            </Panel>
          )}

          {/* The honest caveat, named in the summary line rather than hidden
              behind "More": this page groups by kind because the API does not
              report a category's parent, and a reader looking for a tree deserves
              to know before they conclude there is not one. */}
          <Disclosure summary="Categories are grouped by kind, not by parent">
            The ledger stores which category sits under which, and a category can be
            created with a parent — but no read endpoint returns that link, so this
            page cannot draw the hierarchy and does not pretend to. It groups by{" "}
            <span className="font-mono">kind</span> instead, which is the
            classification every total in this application is computed from.
            <div className="mt-2">
              Nothing here is lost: every category is listed, and the id beside each
              name is the one the transactions page, the rules page and the ledger
              itself all agree on.
            </div>
          </Disclosure>
        </div>

        <NewCategoryPanel />
      </div>
    </AppShell>
  );
};

/* ── One kind ──────────────────────────────────────────────────────────────── */

/* One section per kind, and the section header carries what the kind DOES rather
 * than repeating its name. "Expense" alone says nothing; "Money out. A
 * transaction in this category reduces the balance" is the sentence a reader
 * needs before choosing, and it is stated once here rather than on every row. */
const KindGroup: React.FC<{ kind: CategoryKind; categories: Category[] }> = ({
  kind,
  categories,
}) => {
  /* Two halves, alphabetical inside each.
   *
   * The halves: the user's own categories first, because those are the ones they
   * came to check, then the ones that ship with the ledger.
   *
   * The alphabet: the API's own order is `sort_order, id`, and `sort_order`
   * defaults to 0 on every row this build can create — so the wire order is
   * really "oldest id first", which interleaves a category created today among
   * ones seeded at install time and reads as noise. Alphabetical is what someone
   * scanning an inventory is looking for, and it is stable across reloads. The
   * halves stay separate because THAT ordering carries meaning and the alphabet
   * does not. `localeCompare` with an explicit numeric collation so "Index 2"
   * sorts after "Index 10" rather than before it. */
  const byName = (a: Category, b: Category): number =>
    a.name.localeCompare(b.name, "en", { numeric: true, sensitivity: "base" });
  const ordered = [
    ...categories.filter((category) => !category.is_system).sort(byName),
    ...categories.filter((category) => category.is_system).sort(byName),
  ];

  return (
    <section>
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-5 pb-2 pt-5">
        <h4 className="eyebrow text-foreground">{KIND_LABEL[kind]}</h4>
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          {ordered.length}
        </span>
        <p className="w-full text-[0.8125rem] leading-relaxed text-muted-foreground">
          {KIND_NOTE[kind]}
        </p>
      </header>

      <ul className="divide-y divide-border">
        {ordered.map((category, index) => (
          <CategoryRow key={category.id} category={category} style={stagger(index)} />
        ))}
      </ul>
    </section>
  );
};

/* One category. The name at body size, the id in the mono beside it, and the
 * system mark in words.
 *
 * NO ACTION COLUMN. There is no endpoint to rename or remove a category, so an
 * edit button here would be a control that cannot do what it says. What the row
 * offers instead is the thing that IS reachable: the transactions already filed
 * under it are not reachable either, and inventing either would be worse than
 * leaving the row as the record it is. */
const CategoryRow: React.FC<{ category: Category; style: React.CSSProperties }> = ({
  category,
  style,
}) => (
  <li className="rise-row flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 py-3" style={style}>
    <span className="text-sm font-medium">{category.name}</span>
    <span className="font-mono text-xs tabular-nums text-muted-foreground">
      {category.id}
    </span>
    {category.is_system && (
      /* The word, not a colour and not a disabled button: "system" says what is
         true, a grey row would only say it is different. */
      <span className="rounded-sm border border-border bg-muted px-1.5 py-px text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
        Ships with the ledger
      </span>
    )}
  </li>
);

/* ── Creating one ──────────────────────────────────────────────────────────── */

const NewCategoryPanel: React.FC = () => {
  const { categories, categoriesLoading, categoriesError, createCategory } =
    useCategoriesContext();

  const [name, setName] = React.useState("");
  const [kind, setKind] = React.useState<CategoryKind>("expense");
  const [parentId, setParentId] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<{ name: string; id: number } | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);

  /* The kinds are the server's list when it could be read, and this build's own
     four when it could not. The fallback is not a guess about the vocabulary:
     `CategoryKindSchema` refuses anything else, so a fifth kind can never be sent
     from here — it would fail at the edge with a 422 rather than be stored. */
  const { kinds } = useCategoriesContext();
  /* A kind that only exists server-side must stay selectable, so the option list
     is this build's four filtered by what the server actually reported. Never
     the other way round: a kind the server did not list is not offered, because
     `CategoryKindSchema` would refuse it at the edge with a 422 and the form
     would be offering a choice the ledger cannot store. */
  const offeredKinds = KIND_ORDER.filter(
    (known) => kinds === null || kinds.includes(known),
  );

  const trimmed = name.trim();
  /* Same split as the rule form: a fact about the DATA closes the fields (the
     list could not be read, so there is nothing to pick), and an incomplete form
     gates only the button. Disabling the name box because the name is empty
     would be a form with no way out. */
  const closed = categoriesLoading || categoriesError !== null;
  const disabled = closed || saving;
  const canSubmit = !closed && !saving && trimmed !== "";
  const parent = categories.find((category) => `${category.id}` === parentId) ?? null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (trimmed === "") {
      return;
    }
    setSaving(true);
    setRefused(null);
    setSaved(null);
    try {
      const created = await createCategory({
        name: trimmed,
        kind,
        /* An empty parent field means top level. `null` rather than a string,
           because the field is optional and `undefined` would be a different
           request body from the one a reader of the API expects. */
        ...(parent === null ? {} : { parent_id: parent.id }),
      });
      setSaved({ name: created.name, id: created.id });
      setName("");
      setParentId("");
    } catch (cause) {
      /* The server's own words. A 409 for a duplicate name under the same
         parent and a 422 for a bad kind say different things, and both are more
         useful than anything this form could invent. */
      setRefused(describeError(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      title="New category"
      description="A name, a kind, and optionally a category to sit under."
    >
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {/* The form's own notice, saying a DIFFERENT thing from the page-level one:
            the page notice reports a failed read, this one explains why the
            controls below are closed. Repeating the server's message in both
            places would be two red boxes saying one thing. */}
        {categoriesError !== null && (
          <Notice tone="warning" label="This form is closed">
            The category list could not be read, so there is nothing to point a new
            category at. Creating one now risks duplicating a category that already
            exists, and the ledger would refuse it.
          </Notice>
        )}

        <Field
          label="Name"
          htmlFor="category-name"
          hint="What you will read on a spending breakdown. Up to 200 characters."
        >
          <input
            id="category-name"
            className="field"
            value={name}
            maxLength={200}
            disabled={disabled}
            placeholder="Coffee"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        <Field
          label="Kind"
          htmlFor="category-kind"
          hint={KIND_NOTE[kind]}
        >
          <select
            id="category-kind"
            className="field"
            value={kind}
            disabled={disabled}
            onChange={(event) => setKind(event.target.value as CategoryKind)}
          >
            {offeredKinds.map((offered) => (
              <option key={offered} value={offered}>
                {KIND_LABEL[offered]}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Parent category"
          htmlFor="category-parent"
          hint={
            parent === null
              ? "Optional. Leave empty for a top-level category. The ledger stores the link but no read endpoint returns it, so a new category cannot be shown in a tree."
              : `This category will sit under ${parent.name}.`
          }
        >
          <select
            id="category-parent"
            className="field"
            value={parentId}
            disabled={disabled}
            onChange={(event) => setParentId(event.target.value)}
          >
            <option value="">
              {categories.length === 0 ? "No categories yet" : "Top level — no parent"}
            </option>
            {groupByKind(categories, KIND_ORDER).map((group) => (
              <optgroup key={group.kind} label={KIND_LABEL[group.kind]}>
                {group.categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name} · {category.id}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </Field>

        {saved !== null && (
          <Notice tone="info" label="Created">
            {saved.name} is category{" "}
            <span className="font-mono">{saved.id}</span>. It can now be picked on a
            transaction and pointed at by a rule.
          </Notice>
        )}

        {refused !== null && (
          <Notice tone="error" label="The category was not created">
            {refused}
          </Notice>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={!canSubmit}>
          {saving ? "Creating…" : "Create the category"}
        </button>
      </form>
    </Panel>
  );
};

