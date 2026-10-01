/* Utility: cn — className merger (tailwind-aware).
 * Passes through to clsx/daisyui under the hood; here we provide a
 * tiny hand-rolled version to avoid an extra dependency.
 */
export const cn = (...classes: string[]) => {
  return classes.filter((c) => c && typeof c === "string").join(" ");
};