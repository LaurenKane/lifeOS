/* The aging arithmetic every Upkeep surface shares.
 *
 * One clock, one definition of "since": the dot's state and the sentence beside
 * it come from the same number, so a row can never say "3 days ago" and light
 * up fresh because two files each did their own subtraction. The subtraction
 * normalises both sides to midnight — an upkeep receipted at 23:00 last night
 * is one day old, not zero, and rounding an eleven-hour gap to "today" would
 * make the dot and the sentence disagree for most of every day.
 *
 * The words carry no judgment (ADR 0011, GLOSSARY "Aim"): "today", "6 days
 * ago", "never tracked". There is no term here for late, because there is no
 * due date in the model to be late against.
 */

/** Whole days since `isoDate`, floored at 0 and at midnight. `null` stays
 * `null`: "never" is a fact, and 0 would claim today. */
export const daysSince = (isoDate: string | null): number | null => {
  if (isoDate === null) return null;
  const done = new Date(isoDate);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  done.setHours(0, 0, 0, 0);
  return Math.max(0, Math.round((now.getTime() - done.getTime()) / 86_400_000));
};

/** How long ago, in the fewest words that stay true. */
export const agoPhrase = (days: number | null): string => {
  if (days === null) return "never tracked";
  if (days === 0) return "today";
  return `${days} day${days === 1 ? "" : "s"} ago`;
};
