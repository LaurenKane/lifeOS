/**
 * The y-axis for the net-worth series.
 *
 * This is its own module because it is a decision, not a component. A chart draws
 * whatever data it is handed, so the scale is the only thing standing between a
 * reader and a false impression — and it deserves to be read, tested and argued
 * about on its own rather than found 900 lines down a page file. Keeping it here
 * also keeps `page.tsx` exporting only components, which is what makes a change to
 * this page hot-reload instead of resetting the module.
 *
 * WHAT WENT WRONG, TWICE
 * ---------------------
 * The axis was originally the data's own min and max. On the demo ledger that is a
 * band of €5,224 on a €127,000 position — 4.09% — and the largest single posting in
 * it, a month's salary, is 66.2% of the whole band. Six months of net worth
 * therefore drew as a square wave: four near-vertical jumps, two plateaus, and a
 * cliff off the right edge. Nothing about that picture was true. It is what a chart
 * looks like when the scale is chosen to fill the box rather than to represent the
 * quantity, and on a ledger that is the failure this product cannot afford.
 *
 * The first repair rounded the bounds. Nicer numbers, same distortion — because the
 * error was never the rounding. It was the span.
 *
 * THE THREE OPTIONS, MEASURED
 * --------------------------
 *   1. Auto-scale to the data range. 4.09% of the position, salary at two thirds of
 *      the height. Rejected: it magnifies routine movement into drama, which is the
 *      square wave.
 *   2. Anchor at zero. Honest about magnitude and useless for reading a trend. The
 *      band would occupy 4.0% of the plot and the line would be a smear on the
 *      floor. Zero is the right anchor when the quantity IS the magnitude — a spend
 *      bar in a chart that also shows zero is the case for it. It is the wrong
 *      anchor when the reader's question is which way the value is moving and by
 *      how much, because a net worth is a level, not a quantity measured from a
 *      reference point.
 *   3. A domain wide enough that ordinary monthly variation reads as ordinary.
 *      Chosen.
 *
 * HOW WIDE IS "WIDE ENOUGH"
 * ------------------------
 * `MIN_SPAN_RATIO`: the plot spans at least this fraction of the position it sits
 * on. The idea is that six months of ordinary life — salary in, spending out — is a
 * modest slice of the height, so the shape is a shallow sawtooth and a month reads
 * as a month rather than as an event. On the demo ledger a ratio of 0.12 gives a
 * €20,000 axis: the whole range is 26.1% of the height and the salary step is
 * 17.3%, which is what a salary looks like next to a balance rather than what an
 * earthquake looks like next to a bank balance.
 *
 * `TICKS`: the bounds are then rounded out to a whole number of tick steps, so the
 * axis reads in numbers a person would write rather than in whatever the data
 * happened to do. The rounded bounds are printed on the panel, and the panel says
 * in words that the axis does not start at zero — a truncated axis is only honest
 * if the truncation is visible, and it cannot rest on a reader noticing.
 *
 * WHAT THIS DOES NOT DO
 * --------------------
 * It does not pad by a percentage of the range, and it does not rescale, smooth or
 * clip anything. The data goes in, a domain comes out, and the data is drawn
 * exactly as it arrived. Every value is inside the returned bounds; `tests/
 * overview-axis.test.ts` holds that line.
 */

/** The plot spans at least this fraction of the position it sits on.
 *
 * 0.12, and it is a measured number rather than a taste. On the demo ledger — a
 * €5,224 range on a €127,000 position, so 4.09% — this yields a €20,000 axis: the
 * range is 26.1% of the height and a month's salary step is 17.3%. Both are far
 * from the 100% and 66.2% that made the line a square wave, and both are large
 * enough to read at a glance.
 *
 * Below about 0.08 the sawtooth flattens into a single stroke and the six months
 * stop being six months. Above about 0.3 the plot is mostly empty panel and the
 * line has to be hunted for. This is the middle of that band. */
const MIN_SPAN_RATIO = 0.12;

/** How many tick intervals the plot carries. Four to six reads as an axis; three
 * looks like a bar chart and eight looks like a spreadsheet. */
const TICKS = 5;

/** Steps a person would choose, as multiples of a power of ten.
 *
 * The list has to reach 10× the magnitude, not 5×. `magnitude` is the power of ten
 * just below `wanted / TICKS`, so `wanted / TICKS` can be anywhere in
 * `[magnitude, 10 × magnitude)`. A list topping out at 5 falls off the end and
 * falls back to a step ten times too large, which turns a five-tick axis into a
 * forty-tick one — a table of numbers rather than an axis. */
const NICE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10] as const;

/** A y-domain for a series given in integer minor units. */
export interface AxisDomain {
  /** The lower bound the plot draws, always `<= min`. */
  floor: number;
  /** The upper bound, always `>= max`. */
  ceil: number;
  /** The interval between labelled ticks. */
  step: number;
}

/**
 * Deliberately wide, deliberately rounded, and never clipped.
 *
 * `min`/`max` come off a network response, so the order is not trusted and neither
 * is the pair being finite: a reversed pair would compute a negative span, and a
 * non-finite one would put the line at infinity.
 */
export const axisDomain = (min: number, max: number): AxisDomain => {
  const low = Math.min(min, max);
  const high = Math.max(min, max);
  const middle = (low + high) / 2;

  const range = high - low;
  /* A flat series has no range to magnify, and an all-zero series has no position
     to be wide relative to. Both fall back to a nominal window — the only case
     where `MIN_SPAN_RATIO` has nothing to measure against. */
  const wanted = Math.max(range, Math.abs(middle) * MIN_SPAN_RATIO, 1);

  const magnitude = 10 ** Math.floor(Math.log10(wanted / TICKS));
  const step =
    NICE_STEPS.map((n) => n * magnitude).find((s) => s * TICKS >= wanted) ??
    magnitude * 10;

  return {
    floor: Math.floor((middle - wanted / 2) / step) * step,
    ceil: Math.ceil((middle + wanted / 2) / step) * step,
    step,
  };
};

/**
 * How much of the plot height the data's own range occupies, as a percentage.
 *
 * Stated on the panel, because a reader who can see that six months of movement
 * fills an eighth of the chart knows the chart is not exaggerating, and one who
 * cannot see it has been told.
 */
export const rangeShare = (
  min: number,
  max: number,
  domain: AxisDomain,
): number => {
  const axisRange = domain.ceil - domain.floor;
  if (axisRange <= 0) {
    return 0;
  }
  return (Math.max(min, max) - Math.min(min, max)) / axisRange;
};