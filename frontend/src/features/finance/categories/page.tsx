/* Categories — what a transaction can be filed under, and the making of one more.
 *
 * WHY THIS IS A PAGE AND NOT A DRAWER ON THE RULES SCREEN.
 * A category outlives the rule that points at it and the transaction that uses
 * it, so it needs its own address: a URL a user can come back to, and a place
 * where the whole set is visible at once. The rules screen edits one thing at a
 * time; this is the inventory behind it.
 *
 * KIND IS THE RANK AND THE PARENT IS THE NESTING INSIDE IT.
 * `GET /categories` returns `parent_id` now, so the tree the brief asked for can
 * be drawn from real edges rather than guessed at. It is still drawn INSIDE the
 * kind sections, because `kind` is what the ledger itself reasons about: it
 * decides a balance's sign and what a category can be budgeted as. A page that
 * led with the tree would be asking the reader to hunt for the sign a total
 * carries, and `buildCategoryTree` says the same thing from the other side —
 * the server does not require a child's kind to match its parent's, so a child
 * of another kind is placed at the top of its own section with its parent named
 * rather than drawn under a parent it does not belong beside.
 *
 * THE NESTING IS DRAWN WITH INDENT AND ONE NEUTRAL HAIRLINE, AND NOTHING ELSE.
 * No connector glyphs, no brand colour on the line, no filled chip per level.
 * A payment slip gets hierarchy from ruled columns and indents, and the only
 * rule on this page is `--rule`: a brand-coloured tree line would turn lime into
 * a hairline, which this design bans outright.
 *
 * COLLAPSE IS A NATIVE `<details>` PER PARENT, AND IT STARTS OPEN.
 * The same argument the disclosure component makes — open, close, keyboard
 * operation and the expanded state come from the platform rather than from a
 * button and some state. The tree also says its own size on the parent, so a
 * folded branch never hides how much is in it.
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
import { cn } from "@/lib/utils";
import { describeError } from "@/lib/apiClient";
import {
  buildCategoryTree,
  countUnder,
  flattenTree,
  groupByKind,
  parentNameOf,
  useCategoriesContext,
} from "./use-categories";
import { KIND_LABEL, KIND_NOTE, KIND_ORDER } from "./types";
import type { CategoryNode } from "./use-categories";
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
  /* How many rows sit under a parent rather than at the top of a section. One
   * figure beside the other two: the panel is an inventory, and "how much of
   * this is nested" is the same question as "how much is mine". */
  const nested = categories.filter(
    (category) => category.parent_id !== null && category.parent_id !== category.id,
  ).length;

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
                    {/* Only when there is one. A "0 nested" figure on a flat ledger
                        states an absence in the same voice as a count, which reads
                        as a category the user has not made yet. */}
                    {nested > 0 && (
                      <>
                        {" · "}
                        <span className="font-mono">{nested}</span> under a parent
                      </>
                    )}
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
                    <KindGroup
                      key={group.kind}
                      kind={group.kind}
                      rows={group.categories}
                      every={categories}
                    />
                  ))}
                </div>
              )}
            </Panel>
          )}

          {/* The honest caveat, named in the summary line rather than hidden
              behind "More": this page nests by parent, but a kind stays the
              section a row is listed under, and a reader who assumed otherwise
              deserves to know before they file a transaction somewhere wrong. */}
          <Disclosure summary="A parent and its child can be different kinds">
            The sections here are the <span className="font-mono">kind</span>, and
            the nesting inside one is the parent. <span className="font-mono">kind</span>{" "}
            is the classification every total in this application is computed from —
            it decides a balance's sign and what a category can be budgeted as — so
            it is what the section is named after. The ledger does not insist that a
            child shares its parent's kind, so where one does not, the child is
            listed at the top of its own section and names the parent it belongs to
            rather than being drawn under a row of another kind.
            <div className="mt-2">
              Nothing is hidden either way: every category is on this page, nested or
              not, and the id beside each name is the one the transactions page, the
              rules page and the ledger itself all agree on.
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
const KindGroup: React.FC<{
  kind: CategoryKind;
  /** This kind's rows only. The tree is built inside a section because kind is
   * the rank — see the module header. */
  rows: readonly Category[];
  /** Every category, so a row whose parent is filed under another kind can be
   * named rather than described. */
  every: readonly Category[];
}> = ({ kind, rows, every }) => {
  /* The nesting and the order both come from `buildCategoryTree`, which is where
   * the ordering is argued for: the user's own rows first, then the ones that
   * ship with the ledger, alphabetical inside each half. It is not restated here
   * because two orderings in one screen is one of them being wrong. */
  const tree = buildCategoryTree(rows, every);
  const beneath = tree.reduce((total, node) => total + countUnder(node), 0);
  /* A stagger index carried across the whole section rather than reset per level,
   * so the cascade runs down the page in reading order and a child's delay is
   * not a fresh start beside its parent. */
  let seen = 0;

  return (
    <section>
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-5 pb-2 pt-5">
        <h4 className="eyebrow text-foreground">{KIND_LABEL[kind]}</h4>
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          {rows.length}
        </span>
        <p className="w-full text-[0.8125rem] leading-relaxed text-muted-foreground">
          {KIND_NOTE[kind]}
          {/* What the nesting is worth, in this section's own terms. Stated once
              here rather than as a figure on every parent row, because a parent
              row that counts itself is a column of counts to compare. */}
          {beneath > 0 && (
            <>
              {" "}
              <span className="font-mono">{beneath}</span> of them sit under another.
            </>
          )}
        </p>
      </header>

      <ul className="divide-y divide-border">
        {tree.map((node) => (
          <CategoryRow
            key={node.category.id}
            node={node}
            depth={0}
            style={stagger(seen++)}
          />
        ))}
      </ul>
    </section>
  );
};

/* ── One row ──────────────────────────────────────────────────────────────── */

/** How far in one level of nesting sits, and how far a row at `depth` sits.
 *
 * 20px is enough to read as a level without pushing a long name off a 390px
 * screen, and it is a whole number of the 4px rhythm's own steps rather than an
 * arbitrary fraction.
 *
 * `treeInset` is the one place the two are related: it is where a branch's
 * hairline is drawn, set in the gutter its parent's own row leaves and one half
 * step clear of it. The rule therefore hangs in the PARENT's margin and the
 * children sit to its right — a payment slip's ruled column, rather than a rule
 * floating at the panel edge next to nothing.
 */
const INDENT_REM = 1.25;
const rowInset = (depth: number): number => INDENT_REM * (depth + 1);
const treeInset = (depth: number): number => rowInset(depth) + INDENT_REM / 2;

/** One category and everything under it.
 *
 * A category with children is a native `<details>` that STARTS OPEN, and the
 * `<summary>` is the row itself rather than a control sitting inside it. Two
 * reasons for that shape:
 *
 *   1. The platform then owns the expanded state, the keyboard, and what a screen
 *      reader announces — the same argument that sends the disclosure's triangle
 *      to an authored chevron and keeps the rules screen free of a "show learned"
 *      toggle.
 *   2. A row whose label is its own toggle cannot be read as a link or mistaken
 *      for one, and the chevron is the only thing in the row that looks
 *      pressable.
 *
 * It starts open because a category folded away by default is a category the
 * reader cannot see, and this page exists so the whole set is visible at once. The
 * count on the parent says what a fold would hide, so folding is the reader's own
 * narrowing rather than something done to them.
 *
 * A LEAF IS NOT A `<details>`. A disclosure with nothing to disclose is a control
 * that does nothing, so only a parent carries the chevron and the markup.
 *
 * THE HAIRLINE IS THE TREE LINE. One `--rule` hairline down the left of the
 * children, at the indent they sit at, and nothing else: no connector glyphs, no
 * brand colour, no per-level chip. A payment slip gets its hierarchy from ruled
 * columns, and a lime hairline would be a brand colour on a hairline, which this
 * design bans outright.
 */
const CategoryRow: React.FC<{
  node: CategoryNode;
  /** Levels below this row's own root. 0 at the top of a section, and it only
   * changes for a row the recursion reached through a parent. */
  depth: number;
  style: React.CSSProperties;
}> = ({ node, depth, style }) => {
  const { category, children, detached } = node;
  const beneath = countUnder(node);
  const hasChildren = children.length > 0;

  /* The indent lives here and nowhere else — on the row's own box, set from the
     nesting depth — so a leaf and a parent at the same level line their NAMES up
     exactly. A parent has a chevron and a leaf does not, and if the chevron were
     in the flow rather than in a reserved slot the two would be offset by 12px,
     which reads as a broken column in a list whose whole job is being a column.
     `DEPTH` is passed in rather than derived from the node, because the tree is
     rendered recursively and a node has no way to know how deep it was reached. */
  const row = (
    <div
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-3"
      style={{ paddingLeft: `${rowInset(depth)}rem` }}
    >
      {/* A fixed slot, occupied or empty. This is what keeps the names in a
          column; see above. */}
      <span aria-hidden className="w-3 shrink-0">
        {hasChildren && <Chevron />}
      </span>
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
      {/* What a fold would hide, said before it is folded. */}
      {hasChildren && (
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
          {beneath} under it
        </span>
      )}
      {detached !== null && <Detached parent={detached} />}
    </div>
  );

  if (!hasChildren) {
    return (
      <li className="rise-row" style={style}>
        {row}
      </li>
    );
  }

  return (
    <li className="rise-row" style={style}>
      <details open className="group/branch">
        <summary
          className={cn(
            /* The summary IS the row, so it carries the hover tint of an
               interactive target and no padding of its own. No marker: the
               browser's own triangle is a system glyph at a weight this design
               does not own, which is why the chevron below is authored.

               The rotation is keyed off `&[open]` ON THE SUMMARY ITSELF rather
               than off a named group on the `<details>`. A named group would be
               matched by an ancestor too, so opening "Organic" would also rotate
               the chevron on "Groceries" above it — one branch opening would tilt
               every branch it contains. */
            "block cursor-pointer list-none hover:bg-ground/60",
            "marker:content-none [&::-webkit-details-marker]:hidden",
            "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ink",
            "[&[open]_span>svg]:rotate-180",
          )}
        >
          {row}
        </summary>
        {/* The hairline the children hang from, one step in. `border-l` on the
            list rather than a drawn connector, so it inherits `--rule` like every
            other rule on the page and stops where the branch ends on its own. */}
        <ul
          className="divide-y divide-border border-l border-rule"
          style={{ marginLeft: `${treeInset(depth)}rem` }}
        >
          {children.map((child) => (
            <CategoryRow
              key={child.category.id}
              node={child}
              depth={depth + 1}
              style={{}}
            />
          ))}
        </ul>
      </details>
    </li>
  );
};

/** The disclosure triangle, pointing down when the branch is open.
 *
 * A copy of the chevron `Disclosure` draws rather than a shared one: two shapes in
 * the app would drift, and this one points down where that one points down only
 * because both are the same gesture — so the geometry is identical and it is
 * still authored twice. */
const Chevron: React.FC = () => (
  <svg
    viewBox="0 0 16 16"
    width="12"
    height="12"
    aria-hidden="true"
    focusable="false"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="-ml-1 shrink-0 translate-y-px text-muted-foreground transition-transform duration-150 group-open/branch:rotate-180 motion-reduce:transition-none"
  >
    <path d="M4 6l4 4 4-4" />
  </svg>
);

/**
 * A parent link this tree could not draw, in one quiet line under the name.
 *
 * It is a statement rather than a fix, because the alternative is worse: a reader
 * who knows a category has a parent and sees it sitting at the top of a section
 * would conclude the hierarchy is broken, and would go looking for the bug. Both
 * cases the server permits are named — a parent filed under another kind, and a
 * `parent_id` that names no category at all — and neither is this screen's fault
 * to fix, since there is no endpoint to move a category between kinds.
 */
const Detached: React.FC<{ parent: NonNullable<CategoryNode["detached"]> }> = ({
  parent,
}) => (
  <p className="w-full text-[0.75rem] leading-relaxed text-muted-foreground">
    {parent.why === "other-kind" ? (
      <>
        Filed under <span className="font-medium">{parent.name}</span> (
        <span className="font-mono">{parent.id}</span>), which is listed under
        another kind. A kind decides a balance's sign, so this row stays in its own
        section rather than moving to one it would change the meaning of.
      </>
    ) : parent.why === "cycle" ? (
      <>
        Its parent, category <span className="font-mono">{parent.id}</span>, lists
        this category as ITS parent, so neither can be drawn above the other. Both
        are listed here.
      </>
    ) : (
      <>
        Its parent is <span className="font-mono">{parent.id}</span>, which is not
        among the categories this page could read.
      </>
    )}
  </p>
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
              ? "Optional. Leave empty for a top-level category, or pick one to nest this new category under it."
              : `This category will sit under ${parent.name} on the list.`
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
            {/* Flattened per kind, because a native `<select>` cannot nest: each
                option names the parent it already sits under, so two categories
                called the same thing in different branches can still be told
                apart by choosing between them. */}
            {groupByKind(categories, KIND_ORDER).map((group) => (
              <optgroup key={group.kind} label={KIND_LABEL[group.kind]}>
                {flattenTree(buildCategoryTree(group.categories, categories)).map(
                  (category) => (
                    <option key={category.id} value={category.id}>
                      {parentNameOf(category, categories) === null
                        ? `${category.name} · ${category.id}`
                        : `${category.name} · under ${parentNameOf(category, categories)} · ${category.id}`}
                    </option>
                  ),
                )}
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

