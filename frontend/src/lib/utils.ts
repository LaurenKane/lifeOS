/* Utility: cn — className merger.
 *
 * Hand-rolled in six lines on purpose: `clsx` and `tailwind-merge` are not
 * dependencies of this project, and adding them would change
 * package-lock.json, which CI gates on an approved-dependency ledger. Later
 * classes win, which is what every call site here relies on.
 *
 * Accepts the falsy values a conditional class produces (`condition && "x"`
 * evaluates to `false`, and `x?.y` to `undefined`) so a call site never has to
 * filter before calling.
 */
export const cn = (...classes: Array<string | false | null | undefined>): string =>
  classes.filter((c): c is string => typeof c === "string" && c !== "").join(" ");
