---
name: LifeOS
description: A flat, two-colour ledger for one person's own money, where the number is the page.
colors:
  ground: "#f1f3e6"
  panel: "#fdfef8"
  ink: "#282828"
  ink-quiet: "#5b5851"
  ink-quieter: "#6d6a60"
  lime: "#e7fe54"
  lime-ink: "#565c38"
  lime-ink-deep: "#40461f"
  cyan: "#c0e7ec"
  cyan-ink: "#385f64"
  cyan-ink-deep: "#1d4a4f"
  money-in: "#004b20"
  money-out: "#721212"
  rule: "oklch(0.84 0.014 114 / 0.75)"
typography:
  figure:
    fontFamily: "LifeOS Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "3.5rem"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.02em"
  figure-small:
    fontFamily: "LifeOS Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "1.375rem"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  label:
    fontFamily: "LifeOS Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.14em"
  heading:
    fontFamily: "LifeOS Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  body:
    fontFamily: "LifeOS Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  fine:
    fontFamily: "LifeOS Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  token: "6px"
spacing:
  unit: "4px"
  panel-inset: "24px"
  panel-gap: "24px"
components:
  panel-lime:
    backgroundColor: "{colors.lime}"
    textColor: "{colors.ink}"
    rounded: "{rounded.token}"
    padding: "{spacing.panel-inset}"
  panel-cyan:
    backgroundColor: "{colors.cyan}"
    textColor: "{colors.ink}"
    rounded: "{rounded.token}"
    padding: "{spacing.panel-inset}"
  panel-quiet:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.token}"
    padding: "{spacing.panel-inset}"
  panel-demo-warning:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.lime}"
    rounded: "{rounded.token}"
    padding: "16px 24px"
  stub-review-pending:
    backgroundColor: "{colors.lime}"
    textColor: "{colors.ink}"
    rounded: "{rounded.token}"
    height: "auto"
  stub-review-clear:
    backgroundColor: "{colors.cyan}"
    textColor: "{colors.ink}"
    rounded: "{rounded.token}"
    height: "auto"
  bar-mark:
    backgroundColor: "{colors.ink}"
    height: "2px"
  focus-ring:
    backgroundColor: "{colors.ink}"
---

# Design System: LifeOS

## Overview

**Creative North Star: "The Payment Slip"**

A bankgier is a form whose entire purpose is to get one number trusted. It gets there with boxed
fields, one prominent amount box, and a perforated stub for the part you keep. LifeOS borrows that
grammar, not the artefact: the page opens on a number, everything else is a field beside it, and
the one thing asking for a decision is a tear-off stub.

The world replaced an earlier one — warm cream ground, a Palatino-class display serif, hairline
rules. That world is retired. Two things are worth keeping from why it went: the serif was chosen
to satisfy the offline guarantee, and this world satisfies that guarantee more honestly, by
self-hosting a face instead of asking the platform for one.

This is an **Operate** surface. The visitor comes to read a figure and, once a month, to clear a
queue. Expression never obstructs the task, and the primary figure is always the largest thing on
the screen.

## Colors

### Ground and surfaces

| Token | Value | Use |
|---|---|---|
| `--ground` | `#f1f3e6` | The page. A warm off-white with a green cast, so an acid lime sits *in* the family instead of fighting it. |
| `--panel` | `#fdfef8` | A raised surface. 2–3% lighter and warmer than the ground — **that difference is the entire elevation system.** |

### Brand fills — equal rank

| Token | Value | Use |
|---|---|---|
| `--lime` | `#e7fe54` | A whole panel. Answers *how much*. |
| `--cyan` | `#c0e7ec` | A whole panel. Answers *where it went*. |

**The rule that makes these work: a brand colour is a filled region, never a hairline.** Lime is
never a 1px rule, a border, a link underline or a small dot. It is an area you can point at. On a
typical page lime fills one panel and cyan fills one or two; the equality is in the world, not in
the panel count on any given screen.

### Ink

`--ink` `#282828` is softened near-black, never `#000`. On lime and cyan, secondary text is tinted
from that surface's own hue — `--lime-ink` `#565c38`, `--lime-ink-deep` `#40461f`,
`--cyan-ink` `#385f64`, `--cyan-ink-deep` `#1d4a4f`. **Never drop neutral grey onto a saturated
fill.**

Body greys on the ground: `--ink-quiet` `#5b5851`, `--ink-quieter` `#6d6a60`.

### Money

`--money-in` `#004b20`, `--money-out` `#721212`. Deep, desaturated, and reserved for direction of
travel. A balance takes neither.

### Named rules

- **Lime and cyan are never adjacent in one column.** Two saturated fields stacked read as a
  stripe, not a hierarchy. When a second saturated panel would land next to another, use
  `--panel`.
- **Never a hairline in a brand colour.** The only rule on the page is `--rule`, and it is a
  neutral at 75% alpha.
- **Selection is an explicit alpha, never `color-mix`.** Lightning CSS wraps `color-mix` in
  `@supports` and degrades the fallback to opaque, which would put ink text on an ink ground.
- **Contrast floors are load-bearing, not aspirational:** body ≥ 4.5:1, large text ≥ 3:1. The
  lowest measured value in the shipped Overview is 5.31:1 on cyan.

## Typography

**Two faces, both self-hosted, no third.**

- **Prose: `LifeOS Sans`** — Work Sans, variable, `100 900`. x/cap 0.52. It was chosen over Inter
  (x/cap 0.73) after both were rendered on the real page at 390 and 1440: the lower x-height reads
  lower and wider and does not crowd small labels, and it leaves the numbers to the mono.
- **Figures: `LifeOS Mono`** — JetBrains Mono, variable, `400 800`. **Every monetary figure on every
  surface uses this, without exception.**

**Why figures are mono and must stay that way.** A ledger is read down a column, not across a
row. Proportional figures make decimal points wander and destroy the ability to scan magnitudes.
This is a functional requirement and it outranks any typographic preference. The reference this
world came from set all currency proportionally with no mono anywhere; that was the one place it
was wrong for this product.

The prose face must keep a double-storey `a`, a closed `g` descender, slightly narrow `o`/`i`
apertures, and flat horizontal terminals. Those four traits are what "not curly" means here.

**Self-hosting satisfies the offline guarantee.** The app must run with `--network=none`. A face
served from the app's own origin costs nothing at runtime; a CDN or a downloaded webfont would
break the guarantee. `OFL-WorkSans.txt` and `OFL-JetBrainsMono.txt` ship beside the woff2 files and
must ship with them.

### Scale

`3.5rem` figure · `2.75rem` · `2rem` · `1.375rem` figure-small · `1.0625rem` heading ·
`0.9375rem` · `0.875rem` body · `0.8125rem` · `0.75rem` fine · `0.6875rem` label ·
`0.625rem` micro.

Display caps at `3.5rem`. Tracking floor −0.02em. Labels are the one uppercase size, tracked to
0.14em.

### Named rules

- **A balance carries no `+`.** `+41,005.64` reads as a movement; a position is not one. Negative
  keeps a real U+2212 minus, not an ASCII hyphen, because screen readers announce some hyphens as
  dashes.
- **Category magnitudes are unsigned.** The section heading carries the direction. A minus sign
  repeated down every row of a list says nothing the heading has not already said.
- **No figure carries a currency code.** The unit rides the panel's label (`NET WORTH · EUR`,
  `EUR · SIX MONTHS`), so columns of numbers align and the unit is still stated.

## Layout

Two independent flex columns packing on their own, **not a grid of rows.** A grid row is as tall as
its tallest cell, which stretches a content-height panel to match its neighbour. This was a real
defect caught in review: `items-start` on a grid fixed the panel but left a 300px notch in the
page, because the row itself was still tall.

- **Desktop (≥1024px):** two columns. The wide column takes the lead panel, then the net-worth
  panel, then the six-month series. The narrow column takes the month-by-month bars, the category
  breakdown, and the review stub.
- **Mobile (390px):** one column, lead panel first, unchanged in scale.
- **Unequal column heights are correct.** Do not stretch, do not fill, do not pad.
- 4px base rhythm. 24px panel gaps, 24px panel inset.
- Body measure 65–75ch. Display max 6rem.

### Panel order is a rank, not a layout constant

The panel order lives in one table with explicit ranks, and no class name in the layout JSX names
a panel. **Spending leads because V1 scope is spending**, and a net-worth figure has less to say
until savings and investments exist. When they do, promoting net worth back to first must be a
change to that table, not a rebuild.

## Elevation & Depth

**There is no depth system.** No shadow, no border, no glass, no backdrop blur on any panel.

Panels separate by fill alone. Where a panel needs to separate from its neighbour, it gets a
different fill — not an offset shadow.

**If you flatten a corner, do not add a shadow to compensate.** That reflex is what destroyed the
previous world. Flatness is load-bearing: it is why a colour field reads as a material rather than
a decoration, and adding depth back would undo it.

Two sanctioned exceptions: the focus ring (2px `--ink`, offset 2px) and the demo-data banner, which
is an ink field, not a surface.

## Shapes

**One radius token: 6px.** `--radius-sm` through `--radius-2xl` all resolve to it, deliberately —
there is no ramp to reach for.

The reference this world came from measured 8–12% of a card's own width for panels and full
stadium (`h/2`) for pills. Both were rejected as too round, and both were absolute tokens that read
anywhere from 3.9% to 12.9% of width depending on card size. Here the value is absolute and small
enough to stay quiet at every scale.

Only genuinely circular things are round: an account avatar, a progress ring. A circular button is
a different decision from a rounded rectangle, and it is not the default.

**The perforation.** The review stub's top edge is a repeating notch, cut with a CSS mask rather
than an image. It is reserved for panels representing a detachable, dismissible thing — a review
stub, an import stub — and appears **once per surface**. It is not a motif to repeat.

## Components

- **Panel** — `--panel`, `--lime` or `--cyan` fill. Flat. Content-height. One panel is the loud
  region; nothing else on the page competes.
- **Lead panel** — the lime panel. Period, the figure, the comparison, and the largest category.
  Nothing else.
- **Review stub** — perforated top edge. **Its fill is its state:** cyan when the queue is clear,
  lime when there is something to clear. The perforation is identical through both states, so the
  shape reads before the colour does.
- **Bar mark** — 2px `--ink`. Function, not decoration: it carries proportional share.
- **Disclosure** — native `<details>`. Names the honest caveat it explains rather than saying
  "More", so it is findable by the question it answers.
- **Figure** — mono, always, at every size.
- **Empty state** — says what is missing. Never fills an empty view with a plausible number.
- **Error state** — names the cause and the recovery.

### Known, deliberate positions

Two things are recorded here so a later surface does not repeat them by accident.

- **The demo banner is heavy, and that is the cost of loud honesty.** In demo mode a full-width ink
  banner sits above the lead panel; on a 390px viewport it and the lead panel together take about
  half the screen, so the page opens on the warning rather than the number. **This is demo-mode
  only — the banner does not exist in real mode — and it is a deliberate trade:** the strongest
  possible signal that the figures are invented beats a lighter marker that can be missed. Do not
  soften it, and do not let the same weight appear in real mode.
- **The "next import" line has no endpoint.** No endpoint states it and `GET /api/v1/imports` is
  not mounted. The panel says so in words rather than showing a plausible date. Whether it becomes
  its own panel or is cut is an open direction decision. **Do not assume it is mounted.**

## Do's and Don'ts

### Do

- **Lead with the number that answers the question the user came with.** Currently: spending.
- **Use lime and cyan as whole fills.** If a brand colour would be thinner than a panel, it is the
  wrong colour for that job.
- **Set every monetary figure in `LifeOS Mono`.** Column alignment is the reason.
- **Keep panels content-height** and let column heights differ.
- **State the honest caveat on the surface**, then put the reasoning one click away.
- **Keep every asset local.** No runtime-fetched font, icon or stylesheet. The app runs with
  `--network=none` as a proof of no telemetry.
- **Draw an axis that does not start at zero for a position, and say so.** Net worth is a *level*,
  not a quantity measured from a reference point. Zero-anchoring squeezes six months of movement
  into a fraction of the height. State the bounds, and keep the plot spanning at least 12% of the
  position it sits on.
- **Distinguish "nothing posted" from "nothing changed."** A stretch with no postings is drawn
  dashed — a solid line would claim the balance held still, which the ledger does not say.

### Don't

- **Don't stretch a panel to match its neighbour**, and don't compensate a flat surface with a
  shadow.
- **Don't put a brand colour on a hairline, a border, an underline or a dot.**
- **Don't stack two saturated panels in one column.**
- **Don't add a greeting, an avatar row, or an intro paragraph.** An introduction that restates
  the page's own title is a header wearing a greeting's clothes.
- **Don't add a kicker or eyebrow above a heading.** Banned outright. A label *beside* a heading
  naming a real field is fine.
- **Don't set currency in a proportional face**, and don't use an ASCII hyphen as a minus.
- **Don't put a near-zero bar where the truth is an absence.** An empty month is a labelled gap.
- **Don't silently substitute demo figures for real ones.** Real-empty and demo are distinct states
  and must be visibly distinct.
- **Don't add a second radius token**, and don't reach for a stadium pill.
- **Don't repeat the perforation.** Once per surface, on a genuinely detachable panel.
- **Don't introduce a charting library** before an aggregation endpoint exists to feed it. Net
  worth, spend-by-category and cashflow now have endpoints; anything else does not.
- **Don't put a chart on a page whose data comes from a stub endpoint.** `/review`, `/imports` and
  `/budgets` still have nothing behind them.
- **Don't use colour as the sole carrier of meaning** anywhere.