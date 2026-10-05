/* Overview — the barrel.
 *
 * Every module the page needs is re-exported from one place, so a test or a
 * future surface imports `@/features/finance/overview` rather than reaching into
 * four files and coupling itself to this page's internal shape. */
export { OverviewPage } from "./page";
export { useOverview } from "./use-overview";
export type { OverviewData, OverviewState } from "./use-overview";
export {
  CashflowBucketSchema,
  CashflowSchema,
  NetWorthPointSchema,
  NetWorthSeriesSchema,
  SpendByCategoryPointSchema,
  SpendByCategorySchema,
} from "./types";
export type { CashflowBucket, NetWorthPoint, SpendByCategoryPoint } from "./types";
export { DEMO_CASES, DEMO_REVIEW, buildDemoOverview } from "./demo";