/* Categories feature barrel — the contract, the hook, and the two pages.
 *
 * Two screens rather than one, because the two objects have different lifetimes:
 * a category outlives every rule and transaction that names it and needs an
 * address of its own, while a rule is edited one at a time. */
export type {
  Category,
  CategoryCreateRequest,
  CategoryKind,
  CategoryRule,
  CategoryRuleCreateRequest,
  RuleDeleteResult,
} from "./types";
export { CategoryKindSchema, KIND_LABEL, KIND_ORDER } from "./types";
export {
  groupByKind,
  rulesWithPattern,
  useCategories,
  useCategoriesContext,
} from "./use-categories";
export type { CategoriesState, DeleteRuleOutcome } from "./use-categories";
export { CategoriesProvider } from "./provider";
export { CategoryPicker } from "./category-picker";
export { categoryName } from "./category-name";
export { CategoriesPage } from "./page";
export { RulesPage } from "./rules-page";