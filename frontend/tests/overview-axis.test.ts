/**
 * The net-worth chart's y-axis, as a thing with a contract.
 *
 * This file exists because the axis was wrong three times. It is the one piece of
 * this page that can quietly lie: a chart renders whatever data it is given, so
 * the only thing standing between a reader and a false impression is the scale.
 * The two failures it has produced were
 *
 *   1. SCALED TO THE DATA RANGE. A €5,224 band on a €127,000 position is 4.09%, so
 *      a month's salary — 66.2% of the band — drew as a cliff and six months drew
 *      as a square wave. The picture said "something violent happened" and nothing
 *      violent happened.
 *   2. THE REPAIR WAS TO ROUND THE BOUNDS. Nicer numbers, same distortion. The
 *      error was never the rounding; it was the span.
 *
 * So the tests below are about the span, not the ticks.
 */
import { describe, expect, it } from "vitest";
import { axisDomain, rangeShare } from "@/features/finance/overview/axis";

describe("the net-worth y-axis", () => {
  /* The real demo window, in minor units. This is the case that failed. */
  const DEMO_LOW = 12_468_996;
  const DEMO_HIGH = 12_991_450;

  it("spans far more than the data range", () => {
    const { floor, ceil } = axisDomain(DEMO_LOW, DEMO_HIGH);
    const dataRange = DEMO_HIGH - DEMO_LOW;
    const axisRange = ceil - floor;

    /* The old axis was `dataRange / dataRange` — the line filled the plot exactly,
       which is the bug. The margin has since been set deliberately rather than
       to a number, so this only holds the floor: the plot must be several times
       wider than the data, never the same width. The test after this one is the
       one that pins the actual proportion. */
    expect(axisRange / dataRange).toBeGreaterThan(3);
  });

  it("makes ordinary monthly movement read as ordinary", () => {
    const { floor, ceil } = axisDomain(DEMO_LOW, DEMO_HIGH);
    const axisRange = ceil - floor;

    /* A €5,224 six-month range should occupy a modest slice of the plot — clearly
       visible, so six months still read as six months, and nowhere near the whole
       thing. Under an eighth means the line is a single whisper; over a half means
       we are back to magnifying noise into drama. */
    const share = (DEMO_HIGH - DEMO_LOW) / axisRange;
    expect(share).toBeGreaterThan(0.15);
    expect(share).toBeLessThan(0.35);

    /* And the single largest thing in the data — a salary — should be a step, not
       a cliff. It was 66.2% of the plot when the axis was the data's own range. */
    const salary = 345_702;
    expect(salary / axisRange).toBeLessThan(0.25);
  });

  it("still contains every value in the data", () => {
    /* Widening the axis must never clip the line. A point outside the domain maps
       to a y beyond the viewBox and vanishes, which is a worse lie than a
       dramatised one. */
    const cases: [number, number][] = [
      [DEMO_LOW, DEMO_HIGH],
      [0, 1],
      [-5_000, -1_000],
      [12_500_000, 12_500_000],
      [99, 101],
      [1_000_000_000, 1_200_000_000],
    ];
    for (const [low, high] of cases) {
      const { floor, ceil } = axisDomain(low, high);
      expect(floor).toBeLessThanOrEqual(low);
      expect(ceil).toBeGreaterThanOrEqual(high);
    }
  });

  it("does not start at zero, and says so in the numbers", () => {
    /* A zero anchor would put the demo line in the bottom 4% of the plot. The
       chosen answer is a truncated axis — which is only honest if the truncation
       is visible, so the bounds must be round, printed, and not zero. */
    const { floor, ceil } = axisDomain(DEMO_LOW, DEMO_HIGH);
    expect(floor).toBeGreaterThan(0);
    expect(floor % 100_000).toBe(0);
    expect(ceil % 100_000).toBe(0);
  });

  it("survives a series that never moved", () => {
    /* Zero range. A naive `max - min` domain divides by zero and draws a line at
       infinity; a naive zero-relative one has no position to be relative to. */
    const { floor, ceil } = axisDomain(7_500_000, 7_500_000);
    expect(Number.isFinite(floor)).toBe(true);
    expect(Number.isFinite(ceil)).toBe(true);
    expect(ceil).toBeGreaterThan(floor);
    expect(floor).toBeLessThanOrEqual(7_500_000);
    expect(ceil).toBeGreaterThanOrEqual(7_500_000);
  });

  it("survives a series sitting on zero", () => {
    /* An all-zero ledger is caught upstream as empty, but a domain function that
       divides by zero on the way to that conclusion is a trap for whoever changes
       it next. */
    const { floor, ceil } = axisDomain(0, 0);
    expect(Number.isFinite(floor)).toBe(true);
    expect(Number.isFinite(ceil)).toBe(true);
    expect(ceil).toBeGreaterThan(floor);
  });

  it("does not depend on the argument order", () => {
    /* These come off a network response and a reversed pair must not invert the
       domain, or the line renders mirrored. */
    expect(axisDomain(DEMO_HIGH, DEMO_LOW)).toEqual(axisDomain(DEMO_LOW, DEMO_HIGH));
  });

  it("produces bounds a person would write down", () => {
    /* Ticks a reader can hold in their head: whole steps of a round number, four to
       six of them across the plot. */
    const { floor, ceil, step } = axisDomain(DEMO_LOW, DEMO_HIGH);
    const intervals = (ceil - floor) / step;
    expect(step).toBeGreaterThan(0);
    expect(intervals).toBeGreaterThanOrEqual(3);
    expect(intervals).toBeLessThanOrEqual(8);
    expect(intervals).toBe(Math.round(intervals));
  });

  it("reports the share of height the data occupies, for the panel to print", () => {
    /* The number printed under the chart. A reader who can see that six months of
       movement fills an eighth of the plot knows the chart is not exaggerating,
       and one who cannot see it has at least been told. */
    const domain = axisDomain(DEMO_LOW, DEMO_HIGH);
    const share = rangeShare(DEMO_LOW, DEMO_HIGH, domain);

    expect(share).toBeGreaterThan(0.15);
    expect(share).toBeLessThan(0.35);
    /* The old axis put this at 1.0 — the data filled the plot exactly, which is the
       whole defect in one number. */
    expect(share).not.toBe(1);
  });

  it("cannot divide by a zero-height plot", () => {
    const degenerate = { floor: 0, ceil: 0, step: 1 };
    expect(rangeShare(0, 0, degenerate)).toBe(0);
  });
});