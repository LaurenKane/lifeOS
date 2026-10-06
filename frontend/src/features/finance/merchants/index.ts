/* Merchants feature barrel — the contract, the hook, and the page.
 *
 * One screen rather than two, unlike the categories feature: a merchant and its
 * aliases are the two halves of one filing decision, and the alias form can only
 * offer merchant names if the merchant list is already in scope. Splitting them
 * would mean one of them could not refer to the other. */
export type {
  Merchant,
  MerchantAlias,
  MerchantAliasCreateRequest,
  MerchantCreateRequest,
  MerchantUpdateRequest,
} from "./types";
export {
  AUTO_BAR,
  DEFAULT_ALIAS_CONFIDENCE,
  MerchantAliasSchema,
  MerchantSchema,
  normaliseConfidence,
} from "./types";
export {
  UNFILED_GROUP,
  UNREAD_GROUP,
  aliasesForMerchant,
  byText,
  groupByCategoryKind,
  useMerchants,
  useMerchantsContext,
} from "./use-merchants";
export type { CurationGroup, DeleteOutcome, MerchantsState } from "./use-merchants";
export { MerchantsProvider } from "./provider";
export { MerchantsPage } from "./page";
