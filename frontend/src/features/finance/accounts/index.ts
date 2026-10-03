/* Accounts feature barrel — the slice's public surface.
 * A fifth slice alongside transactions/review/imports/budgets, with the same
 * shape as all of them: types, hook + context, provider, page. */
export type {
  AccountCreateRequest,
  AccountNature,
  AccountSummary,
  AccountType,
} from "./types";
export { useAccounts, useAccountsContext } from "./use-accounts";
export { AccountsProvider } from "./provider";
export { AccountsPage } from "./page";
