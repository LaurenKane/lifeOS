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
 * A DELETE IS BY TEXT, AND THAT IS NOT AN IMPLEMENTATION DETAIL.
 * `DELETE /categories/rules/{pattern}` removes EVERY rule carrying that pattern,
 * hand and learned alike. So the filter after a success is by text and not by id:
 * deleting only the row that was clicked would leave the screen showing a rule
 * the server has already removed. */
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
 * There is a refused branch and the type keeps it: a 404 means nothing matched
 * that pattern, which is a different fact from "it is gone", and reporting the
 * two the same way would let a typo look like an edit. */
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
  deleteRule: (pattern: string) => Promise<DeleteRuleOutcome>;
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
    async (pattern: string): Promise<DeleteRuleOutcome> => {
      /* `encodeURIComponent`, and not defensively: a description pattern is raw
       * statement text and routinely holds a `/`, a `&` or a space. An
       * unencoded one addresses a DIFFERENT path, which for a pattern like
       * "a/b" is a 404 that reads as "no such rule". */
      const path = `${API_PATH}/categories/rules/${encodeURIComponent(pattern)}`;
      let result: RuleDeleteResult;
      try {
        const payload = await apiDelete<unknown>(path);
        result = expectSchema(RuleDeleteResultSchema, payload, "the delete answer");
      } catch (cause) {
        /* A refusal is an answer, not an exception to re-throw: it carries the
         * server's own words and belongs on screen next to the rule it was asked
         * about. Nothing is removed locally in this branch, so a typo leaves the
         * rule exactly where it was. */
        return {
          kind: "refused",
          status: cause instanceof ApiError ? cause.status : 0,
          message: describeError(cause),
        };
      }
      /* EVERY rule carrying that text is gone, so the filter is by text. */
      setRules((previous) =>
        previous.filter((rule) => rule.description_pattern !== pattern),
      );
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

/** Every rule carrying one pattern. `DELETE /rules/{pattern}` removes all of
 * them, so the screen says the count before it deletes rather than after. */
export const rulesWithPattern = (
  rules: readonly CategoryRule[],
  pattern: string,
): CategoryRule[] => rules.filter((rule) => rule.description_pattern === pattern);