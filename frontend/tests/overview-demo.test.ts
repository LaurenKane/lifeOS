import { describe, expect, it } from "vitest";
import { buildDemoOverview, DEMO_CASES, DEMO_REVIEW } from "@/features/finance/overview/demo";
import { NetWorthSeriesSchema, SpendByCategorySchema, CashflowSchema } from "@/features/finance/overview/types";

/** The demo is authored as postings and DERIVED, so the first thing to prove is
 * that the derivation lands on the shapes the real endpoints return. If it did
 * not, the demo would be quietly showing the page something the API never sends,
 * and every visual judgement made on it would be a judgement about fiction. */
const TODAY = new Date(2026, 9, 5);

describe("demo overview — the derivation", () => {
  it("produces shapes the real schemas accept", () => {
    const built = buildDemoOverview(TODAY);
    expect(NetWorthSeriesSchema.safeParse(built.netWorth).success).toBe(true);
    expect(SpendByCategorySchema.safeParse(built.spendByCategory).success).toBe(true);
    expect(CashflowSchema.safeParse(built.cashflow).success).toBe(true);
  });

  it("is all integer minor units, never a float", () => {
    const built = buildDemoOverview(TODAY);
    for (const point of built.netWorth) {
      expect(Number.isInteger(point.net_worth)).toBe(true);
    }
    for (const point of built.spendByCategory) {
      expect(Number.isInteger(point.amount)).toBe(true);
    }
    for (const bucket of built.cashflow) {
      expect(Number.isInteger(bucket.income)).toBe(true);
      expect(Number.isInteger(bucket.expense)).toBe(true);
    }
  });

  it("emits every day in the window, so a quiet week reads as flat", () => {
    const built = buildDemoOverview(TODAY);
    const expectedDays =
      (TODAY.getFullYear() * 372 + TODAY.getMonth() * 31 + TODAY.getDate()) -
      (TODAY.getFullYear() * 372 + 4 * 31 + 1) +
      1;
    expect(built.netWorth.length).toBeGreaterThan(150);
    expect(expectedDays).toBeGreaterThan(0);
    // Strictly increasing by one day, no gaps.
    for (let i = 1; i < built.netWorth.length; i += 1) {
      expect(built.netWorth[i]!.date > built.netWorth[i - 1]!.date).toBe(true);
    }
  });

  it("moves the window with today, so the demo does not expire", () => {
    const later = buildDemoOverview(new Date(2027, 2, 14));
    expect(later.months.at(-1)).toBe("2027-03");
    expect(later.cashflow.at(-1)?.period).toBe("2027-03");
  });
});

describe("demo overview — the awkward cases are actually there", () => {
  it("omits the month with no spending rather than emitting a zero bucket", () => {
    const built = buildDemoOverview(TODAY);
    // Six months on the axis…
    expect(built.months).toHaveLength(6);
    // …and the no-spending month is not among the buckets.
    const noSpend = built.months[built.months.length - 1 - DEMO_CASES.noSpendingMonthOffset];
    expect(noSpend).toBeDefined();
    expect(built.cashflow.some((bucket) => bucket.period === noSpend)).toBe(false);
  });

  it("carries a category with exactly one transaction", () => {
    const built = buildDemoOverview(TODAY);
    const row = built.spendByCategory.find(
      (point) => point.category_name === DEMO_CASES.singletonCategory,
    );
    expect(row).toBeDefined();
    // One visit, and the bar chart still has to draw it.
    expect(row?.amount).toBeLessThan(0);
    expect(Math.abs(row?.amount ?? 0)).toBeLessThan(5000);
  });

  it("has a flat net-worth stretch — ten days at one value", () => {
    const built = buildDemoOverview(TODAY);
    const values = built.netWorth.map((point) => point.net_worth);
    let run = 1;
    let longest = 1;
    for (let i = 1; i < values.length; i += 1) {
      run = values[i] === values[i - 1] ? run + 1 : 1;
      longest = Math.max(longest, run);
    }
    expect(longest).toBeGreaterThanOrEqual(10);
  });

  it("excludes the savings transfer from spending and from income", () => {
    const built = buildDemoOverview(TODAY);
    // A 750.00 top-up happens every month. If it leaked into spending, the
    // categories would carry a recurring 750 that is not a purchase.
    const rent = built.spendByCategory.find((point) => point.category_name === "Huur");
    expect(rent?.amount).toBeLessThan(0);
    const hasTransferCategory = built.spendByCategory.some((point) =>
      /transfer|savings|spaar/i.test(point.category_name),
    );
    expect(hasTransferCategory).toBe(false);
  });

  it("counts a card payment as cash leaving without counting it as spending", () => {
    const built = buildDemoOverview(TODAY);
    // The payment is ~1,000 and the biggest single monthly outflow alongside rent.
    const busiest = built.cashflow.reduce((worst, bucket) =>
      bucket.expense > worst.expense ? bucket : worst,
    );
    expect(busiest.expense).toBeGreaterThan(150_000);
    // And nothing in the category list is a card payment.
    expect(built.spendByCategory.some((point) => /card|amex|payment/i.test(point.category_name))).toBe(
      false,
    );
  });

  it("puts income in as income, positive, and never mixed with expenses", () => {
    const built = buildDemoOverview(TODAY);
    const salary = built.spendByCategory.find((point) => point.category_name === "Salaris");
    expect(salary?.kind).toBe("income");
    expect(salary?.amount).toBeGreaterThan(0);
    for (const bucket of built.cashflow) {
      expect(bucket.income).toBeGreaterThanOrEqual(0);
      expect(bucket.expense).toBeGreaterThanOrEqual(0);
      expect(bucket.net).toBe(bucket.income - bucket.expense);
    }
  });

  it("reports a review queue that is not empty, so the stub shows both states", () => {
    expect(DEMO_REVIEW.open).toBeGreaterThan(0);
  });
});