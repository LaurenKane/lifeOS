/* Categorization rules — every rule the engine reads, in the order it reads
 * them, editable by hand.
 *
 * THE BRIEF THIS SCREEN ANSWERS IS "RULES ARE EDITABLE AND READABLE AS PLAIN
 * TEXT", and the second half is the harder one. The API answers with a flat
 * array in engine order and no grouping, and it would have been easy to render
 * that array as a table of ids — which is unreadable, and which is why the
 * acceptance criterion says plain text.
 *
 * So a rule is rendered as the instruction it is:
 *
 *     "PAYPAL XYZ"  →  Music
 *
 * Two decisions follow from that, and both are visible in `rule-line.tsx`:
 *
 *   - THE ORDER ON SCREEN IS THE ORDER IN THE ENGINE. The list is not re-sorted
 *     by kind, by confidence or by alphabet, because a user reading a rule is
 *     asking "what will this match first", and the array's order is the only
 *     honest answer to that. `priority` is printed on every row so the order is
 *     not merely asserted — the number that produced it is visible.
 *
 *   - LEARNED RULES ARE NOT HIDDEN BEHIND A FILTER. There is no "show learned"
 *     toggle and no tab, because a rule the user cannot see is a rule they will
 *     not fix, and the whole point of an unpaginated endpoint is that this screen
 *     shows the whole set. Hand and learned share one list and are told apart by
 *     a word and a fill.
 *
 * THE DELETE IS BY TEXT AND IT IS NOT INVISIBLE.
 * `DELETE /categories/rules/{pattern}` removes every rule carrying that pattern.
 * Deleting one row can therefore remove several, so the confirmation names the
 * count — and a pattern-less rule is offered no button at all, because there is
 * no text to address it by. Neither case is discovered after the fact.
 *
 * THE CATEGORY PICKER IS ON THIS SCREEN AND NOT ONLY ON THE DETAIL PAGE.
 * A rule is useless without a category, and the two are edited together, so the
 * form carries its own picker rather than sending the user somewhere else to
 * find out what category id 12 is. */
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
import { RuleLine } from "./rule-line";
import { groupByKind, rulesWithPattern, useCategoriesContext } from "./use-categories";
import { KIND_LABEL, KIND_ORDER } from "./types";
import type { CategoryRule } from "./types";

/** A row's cadence, capped so a long rule list arrives as a cascade rather
 * than a backlog. The same pair the transactions and review lists use. */
const STAGGER_MS = 26;
const STAGGER_CAP = 14;
const stagger = (index: number): React.CSSProperties => ({
  animationDelay: `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms`,
});

/** The tier a hand rule is written at. The backend defaults to 100 and stores
 * it, and stating it here is what lets a user understand that their rule
 * outranks everything the system has learned at 500. */
const HAND_PRIORITY = 100;
const LEARNED_PRIORITY = 500;

/** "1 rule" and "2 rules", in a sentence and in a button label. Spelled out
 * because a button that says "Delete 1 rule(s)" is a form that has given up. */
const plural = (count: number, noun: string): string =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;

type Pending = { kind: "created"; pattern: string } | { kind: "deleted"; pattern: string; count: number } | null;
type Refusal = { status: number; message: string; pattern: string } | null;

export const RulesPage: React.FC = () => {
  const {
    categories,
    rules,
    rulesLoading,
    rulesError,
    reloadRules,
    deleteRule,
  } = useCategoriesContext();

  const [busy, setBusy] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState<Pending>(null);
  const [refusal, setRefusal] = React.useState<Refusal>(null);
  const [announcement, setAnnouncement] = React.useState("");
  /* The rule being deleted, held so the confirmation can name it. Clicking
     Delete does not delete: the rule is still on screen and nothing has been
     sent, which is the only way a destructive action stays honest on a screen
     that is otherwise about reading. */
  const [confirming, setConfirming] = React.useState<CategoryRule | null>(null);

  const learned = rules.filter((rule) => rule.is_learned).length;
  const hand = rules.length - learned;

  /* An error with nothing behind it is the whole screen. "No rules" told to a
   * user with a dead backend is a statement about their ledger — and the
   * obvious next move on an empty rule screen is to write a rule that is already
   * there. */
  const unreadable = rulesError !== null && rules.length === 0;

  const askToDelete = (rule: CategoryRule) => {
    setRefusal(null);
    setConfirming(rule);
  };

  const abandonDelete = () => {
    setConfirming(null);
    setAnnouncement("Delete cancelled. The rule is unchanged.");
  };

  const confirmDelete = async () => {
    const rule = confirming;
    /* A rule with no text pattern has nothing to address the endpoint with, so
       there is nothing to send. The button is disabled on the row; this is the
       same fact stated once more where a click would otherwise land. */
    if (rule === null || rule.description_pattern === null) {
      setConfirming(null);
      return;
    }
    const pattern = rule.description_pattern;
    const sharing = rulesWithPattern(rules, pattern).length;
    setConfirming(null);
    setBusy(pattern);
    setRefusal(null);
    const outcome = await deleteRule(pattern);
    setBusy(null);
    if (outcome.kind === "deleted") {
      setPending({ kind: "deleted", pattern, count: sharing });
      setAnnouncement(
        sharing > 1
          ? `${sharing} rules matching ${pattern} were deleted.`
          : `The rule matching ${pattern} was deleted.`,
      );
    } else {
      setRefusal({ status: outcome.status, message: outcome.message, pattern });
      setAnnouncement(
        `Nothing was deleted for ${pattern}. ${outcome.message}`,
      );
    }
  };

  return (
    <AppShell>
      <PageHeader
        title="Categorization rules"
        description={
          <>
            Every rule the engine matches against a transaction description, in the
            order it tries them. A lower priority number is tried first.{" "}
            <strong className="font-medium">Hand-written</strong> rules are at{" "}
            <span className="font-mono">{HAND_PRIORITY}</span> and beat anything the
            system learned on its own at <span className="font-mono">{LEARNED_PRIORITY}</span>
            .
          </>
        }
        actions={
          /* The two halves of one subject, side by side. The masthead carries one
             entry for both, so this is how a user moves between them — and it is
             a link rather than a toggle, because they are two addresses with
             their own URLs, not two states of one control. */
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to="/finance/categories"
              className="btn btn-quiet"
            >
              All categories
            </Link>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={reloadRules}
              disabled={rulesLoading}
            >
              {rulesLoading ? "Reading…" : "Reload"}
            </button>
          </div>
        }
      />

      {/* The one live region on this page. Creating and deleting both change the
          list without a new page appearing, and the row either created or gone
          is not enough of a trace on its own. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem] lg:items-start">
        <div className="space-y-6">
          {/* The read error sits ABOVE the panel, not inside it. When the read
              failed there is no panel — the empty state would be a lie and the
              panel's own error slot is unreachable — and when rules are already
              on screen it sits above them rather than throwing away a readable
              list over a failed re-read. */}
          {rulesError !== null && (
            <Notice tone="error" label="The rules could not be read">
              {rulesError}
              <div className="mt-3">
                <button type="button" className="btn btn-quiet" onClick={reloadRules}>
                  Try again
                </button>
              </div>
            </Notice>
          )}

          {refusal !== null && (
            <Notice
              tone="error"
              label={`Nothing was deleted${refusal.status > 0 ? ` (${refusal.status})` : ""}`}
            >
              {refusal.message}
              <div className="mt-2">
                The rule matching “{refusal.pattern}” is still on the list.
              </div>
            </Notice>
          )}

          {pending !== null && (
            <Notice tone="info" label={pending.kind === "created" ? "Rule added" : "Deleted"}>
              {pending.kind === "created" ? (
                <>
                  A rule matching “{pending.pattern}” now runs at priority{" "}
                  <span className="font-mono">{HAND_PRIORITY}</span>.
                </>
              ) : pending.count > 1 ? (
                <>
                  {pending.count} rules carried the text “{pending.pattern}”, and the
                  delete removes all of them — so all {pending.count} are gone.
                </>
              ) : (
                <>The rule matching “{pending.pattern}” is gone.</>
              )}
            </Notice>
          )}

          {/* The confirmation lives OUTSIDE and ABOVE the panel, not in place of
              the list. Two reasons, and the second is the one that matters:
              the list stays on screen so the rule being deleted can be read
              while the choice is open, and cancelling leaves the screen exactly
              as it was. A modal for one line of text and one delete would be a
              heavier thing than the decision — and it would hide the very rule
              the decision is about. */}
          {confirming !== null && (
            <Notice
              tone="warning"
              label={`Delete ${plural(
                rulesWithPattern(rules, confirming.description_pattern ?? "").length,
                "rule",
              )}?`}
            >
              Every rule carrying the text “{confirming.description_pattern}” is
              removed, not only this row.
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="btn btn-primary" onClick={confirmDelete}>
                  Delete {plural(
                    rulesWithPattern(rules, confirming.description_pattern ?? "").length,
                    "rule",
                  )}
                </button>
                <button type="button" className="btn btn-quiet" onClick={abandonDelete}>
                  Keep {plural(
                    rulesWithPattern(rules, confirming.description_pattern ?? "").length,
                    "rule",
                  )}
                </button>
              </div>
            </Notice>
          )}

          {!unreadable && (
            <Panel
              title="The rule set"
              description={
                rules.length === 0 ? undefined : (
                  <span className="tabular-nums">
                    <span className="font-mono">{rules.length}</span> rules ·{" "}
                    <span className="font-mono">{hand}</span> hand-written ·{" "}
                    <span className="font-mono">{learned}</span> learned
                  </span>
                )
              }
            >
              {rulesLoading && rules.length === 0 ? (
                <Skeleton label="Loading the rules" rows={5} />
              ) : rules.length === 0 ? (
                <EmptyState title="No rules yet.">
                  Nothing has been taught and nothing has been written, so every
                  description reaches the matcher as itself. The first rule comes from
                  correcting a transaction and choosing to remember that payee.
                </EmptyState>
              ) : (
                <ul className="divide-y divide-border">
                  {rules.map((rule, index) => (
                    <RuleLine
                      key={rule.id}
                      rule={rule}
                      category={categories.find((candidate) => candidate.id === rule.category_id)}
                      sharing={rulesWithPattern(rules, rule.description_pattern ?? "").length}
                      busy={busy !== null && rule.description_pattern === busy}
                      onDelete={askToDelete}
                      style={stagger(index)}
                    />
                  ))}
                </ul>
              )}
            </Panel>
          )}

          {/* The rules screen's own explanation, one click away. It answers the
              question the screen provokes — "what happens to my transactions if
              I get this wrong?" — and it is the reasoning, not a caption, so it
              belongs folded. */}
          <Disclosure summary="A rule is a substring, not a regular expression">
            A pattern matches when it appears anywhere in the transaction
            description, so <span className="font-mono">ALBERT HEIJN</span> matches{" "}
            <span className="font-mono">ALBERT HEIJN 1234 AMSTERDAM</span>. Matching is
            case-insensitive and ignores the surrounding whitespace. There is no
            expression syntax to learn, which is the point: a rule you cannot read
            is a rule you will not fix.
            <div className="mt-2">
              The first rule that matches wins, so order is set by priority and not by
              the order of this list. A hand-written rule at{" "}
              <span className="font-mono">{HAND_PRIORITY}</span> is tried before anything
              the system taught itself at <span className="font-mono">{LEARNED_PRIORITY}</span>
              , which is why a correction you write down here sticks.
            </div>
            <div className="mt-2">
              Rules are matched when a description is categorised. Changing one here does
              not re-file transactions that already have a category — correcting those is
              the other page, and one correction at a time is how you find out what you got
              wrong.
            </div>
          </Disclosure>
        </div>

        <NewRulePanel
          onCreated={(pattern) => setPending({ kind: "created", pattern })}
          onFailed={(message) => setAnnouncement(message)}
          busy={busy !== null}
        />
      </div>
    </AppShell>
  );
};

/* ── Writing a rule ────────────────────────────────────────────────────────── */

const NewRulePanel: React.FC<{
  onCreated: (pattern: string) => void;
  onFailed: (message: string) => void;
  busy: boolean;
}> = ({ onCreated, onFailed, busy }) => {
  const { categories, categoriesLoading, categoriesError, createRule } = useCategoriesContext();

  const [pattern, setPattern] = React.useState("");
  const [categoryId, setCategoryId] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState<string | null>(null);
  const [refused, setRefused] = React.useState<string | null>(null);
  const [fieldError, setFieldError] = React.useState<string | null>(null);

  const groups = groupByKind(categories, KIND_ORDER);
  const chosen = categories.find((category) => `${category.id}` === categoryId) ?? null;
  const patternError =
    pattern.trim() === "" && pattern.length > 0
      ? "A pattern of only spaces matches every description. Type the payee's text."
      : null;

  /* Three separate conditions, because they disable DIFFERENT things and
     conflating them is how a form ends up with an untypable text box:
     `closed` is a fact about the data — the categories could not be read, so
     there is nothing to choose; `saving`/`busy` are facts about a request in
     flight. `chosen === null` gates ONLY the submit button: an incomplete form
     is not a disabled form, and disabling the fields a user has to fill in to
     make it complete is a loop with no exit. */
  const closed = categoriesLoading || categoriesError !== null;
  const submitting = saving || busy;
  const canSubmit = !closed && !submitting && pattern.trim() !== "" && chosen !== null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = pattern.trim();
    if (trimmed === "" || chosen === null) {
      return;
    }
    setSaving(true);
    setRefused(null);
    setSaved(null);
    setFieldError(null);
    try {
      const rule = await createRule({
        description_pattern: trimmed,
        category_id: chosen.id,
        priority: HAND_PRIORITY,
      });
      setPattern("");
      setCategoryId("");
      setSaved(
        `“${rule.description_pattern}” now points at ${chosen.name}. It runs at priority ${rule.priority}, ahead of everything the system learned.`,
      );
      onCreated(trimmed);
    } catch (cause) {
      /* The server's own words. A 409 on a duplicate pattern and a 422 on an
         empty one say different things, and both are more useful than anything
         this form could invent. */
      const message = describeError(cause);
      setRefused(message);
      onFailed(`The rule was not saved. ${message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      title="Write a rule"
      description="A substring of the description, and the category it means."
    >
      <form onSubmit={submit} className="space-y-5 p-5" noValidate>
        {categoriesError !== null && (
          <Notice tone="error" label="The categories could not be read">
            {categoriesError}
            <div className="mt-1">
              A rule needs a category to point at, and without the list there is
              nothing to choose.
            </div>
          </Notice>
        )}

        <Field
          label="Description pattern"
          htmlFor="rule-pattern"
          error={patternError ?? fieldError}
          hint="Matched as plain text anywhere in the description. “ALBERT HEIJN” matches “ALBERT HEIJN 1234 AMSTERDAM”."
        >
          <input
            id="rule-pattern"
            className="field font-mono"
            value={pattern}
            maxLength={200}
            disabled={closed || submitting}
            placeholder="PAYPAL"
            onChange={(event) => setPattern(event.target.value)}
          />
        </Field>

        <Field
          label="Category"
          htmlFor="rule-category"
          hint={
            chosen === null
              ? "Where a matching transaction is filed."
              : `A matching transaction is filed as ${chosen.name}.`
          }
        >
          {/* Grouped, because the kind is what the ledger reports on and what
              decides a balance's sign. A flat list of names hides that a category
              called "Savings" is an investment and not a transfer. */}
          <select
            id="rule-category"
            className="field"
            value={categoryId}
            disabled={closed || submitting}
            onChange={(event) => setCategoryId(event.target.value)}
          >
            <option value="">
              {categoriesLoading
                ? "Reading categories…"
                : categories.length === 0
                  ? "No categories yet"
                  : "Choose a category"}
            </option>
            {groups.map((group) => (
              <optgroup key={group.kind} label={KIND_LABEL[group.kind]}>
                {group.categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </Field>

        <p className="text-xs leading-relaxed text-muted-foreground">
          This rule runs at priority <span className="font-mono">{HAND_PRIORITY}</span>,
          ahead of every rule the system taught itself at{" "}
          <span className="font-mono">{LEARNED_PRIORITY}</span>. Only matching is
          affected: transactions that already carry a category keep it.
        </p>

        {saved !== null && <Notice tone="info" label="Added">{saved}</Notice>}
        {refused !== null && (
          <Notice tone="error" label="The rule was not saved">
            {refused}
          </Notice>
        )}

        <button type="submit" className="btn btn-primary w-full" disabled={!canSubmit}>
          {saving ? "Saving…" : "Add this rule"}
        </button>
      </form>
    </Panel>
  );
};