/* ── Icons ─────────────────────────────────────────────────────────────────
 * Three glyphs, one stroke, one grid.
 *
 * The two vendored prose faces and the vendored mono were all checked for arrow
 * and tick coverage and NONE of them has U+2192, U+2713 or U+25B8 — a caret or a
 * tick written as a Unicode character renders as a fallback glyph from whatever
 * font the OS happens to have, which is a different weight and a different
 * optical size sitting inside this design. So the few marks this app needs are
 * drawn here instead.
 *
 * One 16px grid, 1.5 stroke, round caps and joins, no fills, `currentColor`
 * throughout — so a mark is the same object at every size it appears and takes
 * the colour of the text it sits beside.
 */
import React from "react";

const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  /* An SVG without this is announced twice by some screen readers. */
  "aria-hidden": true,
  focusable: false,
} as const;

/** Up-right. The direction a balance moved. */
export const ArrowUpRight: React.FC<{ className?: string }> = ({ className }) => (
  <svg {...base} className={className}>
    <path d="M4.5 11.5 11.5 4.5" />
    <path d="M5.75 4.5h5.75v5.75" />
  </svg>
);

/** Down-right. */
export const ArrowDownRight: React.FC<{ className?: string }> = ({ className }) => (
  <svg {...base} className={className}>
    <path d="M4.5 4.5 11.5 11.5" />
    <path d="M5.75 11.5h5.75V5.75" />
  </svg>
);

/** Right. The only affordance mark: where a control leads. */
export const ArrowRight: React.FC<{ className?: string }> = ({ className }) => (
  <svg {...base} className={className}>
    <path d="M3 8h10" />
    <path d="M9 4l4 4-4 4" />
  </svg>
);