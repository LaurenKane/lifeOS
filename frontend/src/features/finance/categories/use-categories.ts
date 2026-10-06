/* Categories hook — owns the category list, the kind list, the rule set and
 * the three writes.
 *
 * THE RULE SET IS NOT PAGINATED, AND NEITHER IS THIS HOOK.
 * `GET /categories/rules` returns every rule — hand and learned — in engine
 * order, because a rule the user cannot see is a rule they will not fix. A hook
 * that paged that list, or that collapsed learned rules into a summary, would
 * reintroduce the gap the endpoint deliberately closes.
 *
 * FAILURES ARE STATE, NOT EMPTINESS.
 * `.catch(() => setData([]))` is how a dead backend becomes "you have no
 * categories" — and on this surface that is a lie a user would act on, because
 * the obvious next move is to create the category they were about to file a
 * transaction under, and the ledger will then refuse a duplicate.
 *
 * A DELETE IS BY RULE ID, AND THAT IS NOT AN IMPLEMENTATION DETAIL.
 * `DELETE /categories/rules/{rule_id}` removes exactly one row. So the filter
 * after a success is by id and not by text: two rules can carry one pattern,
 * and removing both because they share a string would take out a rule the user
 * did not click on. An id is also the only address that survives a pattern —
 * `bakker/straat` is one string but two path segments. */
import React from "react";
import {
  API_PATH,
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  describeError,
  expectSchema,
} from "@/lib/apiClient";
import {
  CategoryKindListSchema,
  CategoryListSchema,
  CategoryRuleListSchema,
  RuleDeleteResultSchema,
  CategorySchema,
  CategoryRuleSchema,
} from "./types";
import type {
  Category,
  CategoryCreateRequest,
  CategoryKind,
  CategoryRule,
  CategoryRuleCreateRequest,
  RuleDeleteResult,
} from "./types";

/** What a delete attempt actually did.
 *
 * There is a refused branch and the type keeps it: a 404 means no rule carries
 * that id, which is a different fact from "it is gone", and reporting the two
 * the same way would let a stale row look like an edit. */
export type DeleteRuleOutcome =
  | { kind: "deleted"; result: RuleDeleteResult }
  | { kind: "refused"; status: number; message: string };

export type CategoriesState = {
  categories: Category[];
  categoriesLoading: boolean;
  categoriesError: string | null;
  /** `null` means the kind list could not be read; the screen degrades to the
   * kinds this build knows and says so rather than blocking. */
  kinds: CategoryKind[] | null;
  rules: CategoryRule[];
  rulesLoading: boolean;
  rulesError: string | null;
  reloadCategories: () => void;
  reloadRules: () => void;
  createCategory: (input: CategoryCreateRequest) => Promise<Category>;
  createRule: (input: CategoryRuleCreateRequest) => Promise<CategoryRule>;
  /** Takes a RULE ID, not a pattern. See the header: an id is the only address
   * that survives a pattern holding a slash, and it removes exactly one rule. */
  deleteRule: (ruleId: number) => Promise<DeleteRuleOutcome>;
};

export const useCategories = (): CategoriesState => {
  const [categories, setCategories] = React.useState<Category[]>([]);
  const [categoriesLoading, setCategoriesLoading] = React.useState(true);
  const [categoriesError, setCategoriesError] = React.useState<string | null>(null);
  const [categoryAttempt, setCategoryAttempt] = React.useState(0);

  const [rules, setRules] = React.useState<CategoryRule[]>([]);
  const [rulesLoading, setRulesLoading] = React.useState(true);
  const [rulesError, setRulesError] = React.useState<string | null>(null);
  const [ruleAttempt, setRuleAttempt] = React.useState(0);
  const [kinds, setKinds] = React.useState<CategoryKind[] | null>(null);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/categories`)
      .then((payload) => {
        if (current) {
          setCategories(expectSchema(CategoryListSchema, payload, "categories"));
          setCategoriesError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setCategoriesError(describeError(cause));
        }
      })
      .finally(() => {
        if (current) {
          setCategoriesLoading(false);
        }
      });

    return () => {
      current = false;
    };
  }, [categoryAttempt]);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/categories/kinds`)
      .then((payload) => {
        if (current) {
          setKinds(expectSchema(CategoryKindListSchema, payload, "category kinds"));
        }
      })
      .catch(() => {
        /* Deliberately NOT surfaced as an error, and here is why. The kind list
         * refines a picker that is already usable: every kind this build can
         * send is in `KIND_ORDER`, so the screen degrades to exactly the kinds
         * the create form would have offered anyway. Turning a refinement into
         * a blocking error would make a working screen unreadable over a list
         * of four words. `kinds` staying null is the flag, and the screen says
         * so in one line where it matters. */
        if (current) {
          setKinds(null);
        }
      });

    return () => {
      current = false;
    };
  }, []);

  /* The rule list is read here, once, with everything else. That is one request
   * per page load that only one screen uses — and the honest trade for a list
   * that is the whole point of a screen which must never paginate. A lazy read
   * would be defensible too; it is not taken because the provider already holds
   * this page's other data, and two reads of the same tree on one screen is a
   * worse thing to explain than one extra GET. */
  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/categories/rules`)
      .then((payload) => {
        if (current) {
          setRules(expectSchema(CategoryRuleListSchema, payload, "categorization rules"));
          setRulesError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setRulesError(describeError(cause));
        }
      })
      .finally(() => {
        if (current) {
          setRulesLoading(false);
        }
      });

    return () => {
      current = false;
    };
  }, [ruleAttempt]);

  const reloadCategories = React.useCallback(() => {
    setCategoriesError(null);
    setCategoriesLoading(true);
    setCategoryAttempt((n) => n + 1);
  }, []);

  const reloadRules = React.useCallback(() => {
    setRulesError(null);
    setRulesLoading(true);
    setRuleAttempt((n) => n + 1);
  }, []);

  const createCategory = React.useCallback(
    async (input: CategoryCreateRequest): Promise<Category> => {
      const created = await apiPost<unknown, unknown>(`${API_PATH}/categories`, input);
      const category = expectSchema(CategorySchema, created, "the new category");
      /* Appended rather than re-read. A 201 is the server's own confirmation,
       * and the only order this API exposes is the row it handed back — a
       * second request would learn nothing the response did not already say. */
      setCategories((previous) => [...previous, category]);
      return category;
    },
    [],
  );

  const createRule = React.useCallback(
    async (input: CategoryRuleCreateRequest): Promise<CategoryRule> => {
      const created = await apiPost<unknown, unknown>(`${API_PATH}/categories/rules`, input);
      const rule = expectSchema(CategoryRuleSchema, created, "the new rule");
      /* Inserted at its engine position, not appended. The screen shows the list
       * in the order the matcher reads it, and a rule appearing at the bottom
       * under a priority it does not hold is a lie about which rule wins. */
      setRules((previous) =>
        [...previous, rule].sort((a, b) => a.priority - b.priority || a.id - b.id),
      );
      return rule;
    },
    [],
  );

  const deleteRule = React.useCallback(
    async (ruleId: number): Promise<DeleteRuleOutcome> => {
      /* An integer is interpolated, never encoded — and that is the whole
       * reason this endpoint exists rather than the one it replaced. A
       * description pattern is raw statement text and routinely holds a `/`, a
       * `&` or a space; as one path segment it could only ever be addressed by
       * percent-encoding the separator itself, and a rule like `bakker/straat`
       * was on screen with no way to remove it. `encodeURIComponent` cannot fix
       * that, because `%2F` inside a segment is a character, not a separator —
       * so `String(3)` is the only encoding here and there is nothing to get
       * wrong. */
      const path = `${API_PATH}/categories/rules/${String(ruleId)}`;
      let result: RuleDeleteResult;
      try {
        const payload = await apiDelete<unknown>(path);
        result = expectSchema(RuleDeleteResultSchema, payload, "the delete answer");
      } catch (cause) {
        /* A refusal is an answer, not an exception to re-throw: it carries the
         * server's own words and belongs on screen next to the rule it was asked
         * about. Nothing is removed locally in this branch, so a stale row leaves
         * the rule exactly where it was. */
        return {
          kind: "refused",
          status: cause instanceof ApiError ? cause.status : 0,
          message: describeError(cause),
        };
      }
      /* ONE rule is gone, so the filter is by id. Filtering by pattern instead
       * would drop a twin that carries the same text and is still stored. */
      setRules((previous) => previous.filter((rule) => rule.id !== ruleId));
      return { kind: "deleted", result };
    },
    [],
  );

  return {
    categories,
    categoriesLoading,
    categoriesError,
    kinds,
    rules,
    rulesLoading,
    rulesError,
    reloadCategories,
    reloadRules,
    createCategory,
    createRule,
    deleteRule,
  };
};

/* The provider owns the fetch; pages read through this context. */
export const CategoriesContext = React.createContext<CategoriesState | null>(null);

export const useCategoriesContext = (): CategoriesState => {
  const state = React.useContext(CategoriesContext);
  if (state === null) {
    throw new Error(
      "useCategoriesContext must be used within a <CategoriesProvider>",
    );
  }
  return state;
};

/**
 * Group a category list by kind, in `order`, dropping an empty group.
 *
 * Shared by the create form's category picker, the rule form's picker and the
 * tree, because a picker offering "Other" beside a tree calling it
 * "Miscellaneous" is two names for one thing.
 */
export const groupByKind = (
  categories: readonly Category[],
  order: ReadonlyArray<CategoryKind>,
): ReadonlyArray<{ kind: CategoryKind; categories: Category[] }> =>
  order
    .map((kind) => ({
      kind,
      categories: categories.filter((category) => category.kind === kind),
    }))
    .filter((group) => group.categories.length > 0);

/** Every rule carrying one pattern.
 *
 * No longer a delete concern — `DELETE /rules/{rule_id}` removes one rule — but
 * still a wording one: two rules can share a text, and the screen tells the
 * reader that the row they are deleting is the only one that goes. */
export const rulesWithPattern = (
  rules: readonly CategoryRule[],
  pattern: string,
): CategoryRule[] => rules.filter((rule) => rule.description_pattern === pattern);

/* ── The tree ───────────────────────────────────────────────────────────── */

/** One category and what sits under it, at one level of one kind section. */
export type CategoryNode = {
  category: Category;
  children: CategoryNode[];
  /** The parent link that could NOT be drawn above this row, and why.
   *
   * `null` on every row the tree draws as it stands. It is not an error field:
   * the server lets a child be filed under a parent of another kind, lets a
   * `parent_id` name nothing at all, and lets two categories name each other, and
   * all three are facts about the data rather than about this screen. They are
   * surfaced on the row because a reader who knows a category has a parent and
   * sees it at the top of a section would otherwise conclude the hierarchy is not
   * being drawn. */
  detached: { name: string; id: number; why: DetachedReason } | null;
};

/** Why a row sits at the top of a section while naming a parent.
 *
 * Three cases the server permits and this screen cannot draw away:
 * `other-kind` the parent is filed under a different kind, so the edge crosses a
 * section boundary; `not-listed` the `parent_id` names no category this page
 * read; `cycle` the parent and its child name each other, so neither can be
 * drawn above the other. */
export type DetachedReason = "other-kind" | "not-listed" | "cycle";

/**
 * Nest one kind's categories by `parent_id`.
 *
 * `kind` IS THE RANK AND `parent_id` IS THE NESTING, not the other way round.
 * `kind` decides a balance's sign and what a category can be budgeted as, so a
 * section must hold one kind and no other — and the server does not require a
 * child's kind to match its parent's. So a child whose parent is filed under
 * another kind cannot be drawn under it without putting an `income` row in the
 * middle of the `expense` section, which is the one thing this page must never
 * do. It is placed at the top of its own section instead, carrying `detached`
 * so the row can name the parent it belongs to. The alternative — dropping the
 * parent link silently — would show the user a hierarchy that quietly disagrees
 * with the ledger.
 *
 * The `seen` set is not defensive padding. The server does not forbid a cycle
 * (`parent_id` is a plain self-reference and a seed or a direct SQL write could
 * close one), and a recursive walk over a cycle never returns. A cycle is
 * reported as `not-listed` rather than crashing the page.
 *
 * Ordering is the same at every level — the user's own rows first, then the ones
 * that ship with the ledger, alphabetical inside each half — because that half
 * carries meaning and the alphabet does not.
 */
export const buildCategoryTree = (
  section: readonly Category[],
  every: readonly Category[],
): CategoryNode[] => {
  const inSection = new Set(section.map((category) => category.id));
  const byId = new Map(every.map((category) => [category.id, category]));
  const byName = (a: Category, b: Category): number =>
    a.name.localeCompare(b.name, "en", { numeric: true, sensitivity: "base" });
  const ordered = (rows: readonly Category[]): Category[] => [
    ...rows.filter((category) => !category.is_system).sort(byName),
    ...rows.filter((category) => category.is_system).sort(byName),
  ];

  const children = new Map<number, Category[]>();
  const roots: Category[] = [];
  for (const category of section) {
    if (category.parent_id === null) {
      roots.push(category);
    } else if (!inSection.has(category.parent_id)) {
      roots.push(category);
    } else {
      const bucket = children.get(category.parent_id) ?? [];
      bucket.push(category);
      children.set(category.parent_id, bucket);
    }
  }

  /* Whether a parent link closes a cycle. `parent_id` is a plain self-reference
   * and nothing forbids closing a loop, so this follows the chain upward with a
   * step budget of one per row in the section: revisiting an id means a cycle,
   * and running out of steps means one too. */
  const inCycle = (category: Category): boolean => {
    const chain = new Set<number>([category.id]);
    let cursor = category;
    for (let step = 0; step < section.length; step += 1) {
      if (cursor.parent_id === null) {
        return false;
      }
      if (chain.has(cursor.parent_id)) {
        return true;
      }
      chain.add(cursor.parent_id);
      const next = byId.get(cursor.parent_id);
      if (next === undefined) {
        return false;
      }
      cursor = next;
    }
    return true;
  };

  const detachedOf = (category: Category): CategoryNode["detached"] => {
    if (category.parent_id === null) {
      return null;
    }
    /* A category inside a cycle IS its own problem: its parent link points at a
       row that is equally unplaceable, so it is reported as a cycle whatever the
       parent happens to be. */
    if (inCycle(category)) {
      return { name: `category ${category.parent_id}`, id: category.parent_id, why: "cycle" };
    }
    const parent = byId.get(category.parent_id);
    if (parent === undefined) {
      return { name: `category ${category.parent_id}`, id: category.parent_id, why: "not-listed" };
    }
    return inSection.has(parent.id)
      ? null
      : { name: parent.name, id: parent.id, why: "other-kind" };
  };

  /* `placed` is the set of rows already in the forest. It does the work `seen`
   * used to: a row reached once is never reached again, so a cycle cannot send
   * this walk round and round, and one shared set across every root also stops a
   * row appearing twice if two roots somehow claim it. */
  const placed = new Set<number>();
  /* `detached` describes a ROW AT THE TOP OF ITS SECTION that has a parent link
     the tree did not draw above it. A row actually drawn under its parent has
     nothing to declare — it is where its own `parent_id` says it should be, even
     when the branch above it is itself a cycle member. Saying otherwise would
     have every row in a cycle apologise for a link the screen did draw. */
  const walk = (category: Category, root: boolean): CategoryNode => {
    placed.add(category.id);
    const kids = (children.get(category.id) ?? []).filter((kid) => !placed.has(kid.id));
    return {
      category,
      detached: root ? detachedOf(category) : null,
      children: ordered(kids).map((kid) => walk(kid, false)),
    };
  };

  const forest = ordered(roots).map((root) => walk(root, true));
  /* A cycle leaves rows no root can reach — two categories parented to each other
   * have no root between them at all. They are added as roots of their own so the
   * page still lists every category it read: `GET /categories` returned the row,
   * and dropping it because its links are broken would leave the user missing a
   * category they have. `detached` on it names the parent it could not be drawn
   * under, so the row is not silently claiming to be top-level. */
  /* The `placed` test is INSIDE the loop, not in a filter above it: walking one
     orphan places its whole reachable branch, so the next orphan may already be on
     the screen. Filtering first would take a snapshot and push the second member
     of a two-category cycle a second time — a row the user has, listed twice. */
  for (const orphan of ordered(section)) {
    if (!placed.has(orphan.id)) {
      forest.push(walk(orphan, true));
    }
  }
  return forest;
};

/** How many categories sit anywhere under one node, at any depth.
 *
 * Stated on the parent's row so a collapsed branch never hides a count: the
 * reader learns that two rows went under a fold without opening it. */
export const countUnder = (node: CategoryNode): number =>
  node.children.reduce((total, child) => total + 1 + countUnder(child), 0);

/** The parent a category names, or `null` when it is top-level or the link
 * names nothing this list holds.
 *
 * For the one control that cannot nest — a native `<select>` — so an option can
 * say which branch it is in. Two categories can share a name in two branches, and
 * "Coffee" twice in a picker is a choice the user cannot make on purpose. */
export const parentNameOf = (
  category: Category,
  every: readonly Category[],
): string | null => {
  if (category.parent_id === null) {
    return null;
  }
  const parent = every.find((row) => row.id === category.parent_id);
  return parent === undefined ? null : parent.name;
};

/** Every category in a forest, parents before children, for the pickers.
 *
 * A native `<select>` cannot nest, so the pickers that use this list say the
 * parent in words instead — `rules-page.tsx` and `category-picker.tsx` both
 * build their own `<optgroup>` by kind and want the same order underneath it. */
export const flattenTree = (nodes: readonly CategoryNode[]): Category[] =>
  nodes.flatMap((node) => [node.category, ...flattenTree(node.children)]);