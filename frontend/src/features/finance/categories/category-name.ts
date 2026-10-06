/** How a category is named in a sentence that is not a list of options.
 *
 * Its own module rather than a second export of `category-picker.tsx`, for two
 * reasons and the first is mechanical: mixing a component and a plain function
 * in one file breaks React Fast Refresh, and the lint rule that catches it is
 * `react/only-export-components`.
 *
 * The second is about where the function belongs. It is used by the transactions
 * LIST, by the detail page and by the picker, so importing it from the picker
 * would make all three depend on a component module to get a string — and the
 * list would then be rendering a component module's worth of imports for one
 * sentence.
 */
import type { Category } from "./types";

/**
 * `null` and `undefined` both mean "the list did not hold this one", which is a
 * real case: a category read that failed, or a record naming a category this
 * build cannot see. Neither renders as blank — the id is printed instead, because
 * a blank next to a category id looks like a rendering fault rather than a fact
 * about what could be read.
 */
export const categoryName = (
  category: Category | null | undefined,
  id: number,
): string => (category === null || category === undefined ? `category ${id}` : category.name);