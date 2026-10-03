---
name: LifeOS
description: Many banks, one ledger — a confluence of accounts resolved into a single quiet statement.
colors:
  ledger-ink: "oklch(0.3 0.035 258)"
  ink-focus: "oklch(0.62 0.09 248)"
  paper: "oklch(0.984 0.006 85)"
  paper-tint: "oklch(0.955 0.008 88)"
  ink: "oklch(0.235 0.014 75)"
  ink-soft: "oklch(0.505 0.016 75)"
  rule: "oklch(0.893 0.011 85)"
  rule-strong: "oklch(0.855 0.013 85)"
  money-out: "oklch(0.48 0.155 28)"
  money-in: "oklch(0.42 0.085 158)"
  alarm: "oklch(0.51 0.16 27)"
  accent-wash: "oklch(0.94 0.028 248)"
  selection: "oklch(0.235 0.014 75 / 0.18)"
typography:
  display:
    fontFamily: "\"Iowan Old Style\", \"Palatino Linotype\", Palatino, \"Book Antiqua\", Georgia, \"Times New Roman\", serif"
    fontSize: "1.875rem"
    fontWeight: 400
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "\"Iowan Old Style\", \"Palatino Linotype\", Palatino, \"Book Antiqua\", Georgia, \"Times New Roman\", serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "-0.025em"
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.625
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Roboto, \"Helvetica Neue\", Arial, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.14em"
  amount:
    fontFamily: "ui-monospace, SFMono-Regular, \"SF Mono\", Menlo, Consolas, \"Liberation Mono\", monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.625
rounded:
  hair: "0.125rem"
  sm: "0.25rem"
  chip: "0.3rem"
  md: "0.375rem"
  lg: "0.4375rem"
  xl: "0.75rem"
spacing:
  hair: "0.125rem"
  tight: "0.25rem"
  snug: "0.5rem"
  base: "0.75rem"
  row: "0.875rem"
  relaxed: "1rem"
  loose: "1.5rem"
  section: "2rem"
  page: "2.5rem"
  deep: "6rem"
components:
  button-primary:
    backgroundColor: "{colors.ledger-ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.75rem"
  button-quiet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.75rem"
  button-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.alarm}"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.75rem"
  field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.5rem 0.625rem"
  panel:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.lg}"
  notice:
    backgroundColor: "{colors.paper-tint}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
  amount-in:
    textColor: "{colors.money-in}"
    typography: "{typography.amount}"
  amount-out:
    textColor: "{colors.money-out}"
    typography: "{typography.amount}"
  masthead:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    height: "3.5rem"
---

# Design System: LifeOS

## Overview

**Creative North Star: "The Confluence"**

LifeOS gathers money that lives in scattered places — a Rabobank current account, a Revolut balance, an Amex card statement, a PDF exported by hand — and nets all of it into one double-entry ledger whose balance rule is enforced by the database itself. The interface is that confluence made visible: several currents arriving at one page and resolving into a single, quiet, trustworthy statement. When a reader sees a transaction, they should be able to trust that five different ingestion paths and three different banks produced it and it still balances.

The world is **warm and calm**. The ground is warm off-white paper, not white and not grey; the ink is warm near-black, never pure black. Nothing on the page is louder than it needs to be. There is no gradient, no glass, no glow, no pill-shaped chrome, and no shadow that lifts a card off the surface. Restraint is not the absence of design here — restraint *is* the design. A reader should feel they are looking at a well-kept document rather than a dashboard.

Depth comes from **paper**, not from elevation. Surfaces separate with a 1px hairline in a warm grey, the way a ruled ledger page separates a heading from its entries. The single shadow in the system is a whisper on the Panel: a zero-blur 1px top-light that reads as the cut edge of a sheet catching light, plus a 3px settle underneath. That is the entire vocabulary.

Money is treated as the most serious thing on the page. Amounts are set in a monospace face with tabular figures so a column lines up on the decimal point and a flipped sign cannot hide in a crowd. Direction is carried by the sign glyph **first** and colour second, because colour alone is not a signal that survives a monochrome print, a screen reader, or colour-blind vision.

**Key Characteristics:**

- Warm paper ground, warm near-black ink, hairline rules instead of boxes.
- Serif for anything that names a thing; platform sans for prose; monospace for money.
- One accent — Ledger Ink — spent only on things you can act on.
- Motion is a single authored moment: the page's blocks arrive in a short cascade.
- Nothing is fetched at runtime. The app must run with the network switched off.

## Colors

A warm-neutral paper palette with exactly one cool accent, and two semantic colours reserved for the direction of money.

### Primary

- **Ledger Ink** (oklch 0.3 0.035 258): the only actionable colour in the system. Primary buttons, the active navigation underline, the focus ring's darker sibling. It appears on ≤10% of any screen. Its rarity is the point — if Ledger Ink is everywhere, it means nothing.
- **Focus Ink** (oklch 0.62 0.09 248): the keyboard focus ring, lifted in lightness from Ledger Ink so it reads against both the paper ground and the ink-blue it sits near.

### Neutral

- **Paper** (oklch 0.984 0.006 85): the ground. Warm off-white — hue 85 is a cream, not a grey. Used for the page, panels, and every input.
- **Paper Tint** (oklch 0.955 0.008 88): the recessed surface — hover states, disabled fields, segmented-control tracks.
- **Ink** (oklch 0.235 0.014 75): body text. Warm near-black at hue 75; never `#000`, which reads as a hole in warm paper.
- **Ink Soft** (oklch 0.505 0.016 75): secondary text, labels, hints. Same warm hue as Ink so muted text looks like lighter ink rather than grey paint.
- **Rule** (oklch 0.893 0.011 85): every hairline — borders, row dividers, the masthead's bottom edge.
- **Rule Strong** (oklch 0.855 0.013 85): input strokes, one step darker than Rule so a field reads as an input rather than as a divider.

### Semantic Money

- **Money Out** (oklch 0.48 0.155 28): a burnt red, not a fire-engine red. Checked against Paper for contrast at body size.
- **Money In** (oklch 0.42 0.085 158): a deep green, deliberately desaturated so a column of them does not shimmer.
- **Alarm** (oklch 0.51 0.16 27): destructive actions and error notices. Distinct from Money Out on purpose — spending is not an error.

### Accent Wash

- **Accent Wash** (oklch 0.94 0.028 248): a barely-there ink-blue for selected or emphasised regions. A tint of Ledger Ink, not a second accent.

### Named Rules

**The One Ink Rule.** Ledger Ink appears only on something the reader can act on — a button, a link, the active nav item, a focus ring. Never on a heading, a container, or a large area. If a screen is mostly ink-blue, the accent has stopped being an accent.

**The Sign First Rule.** The direction of money is carried by the sign glyph before it is carried by colour. Colour is the second signal, never the only one. This is why the `Amount` component renders a screen-reader-only "money out"/"money in" — the `−` is read aloud as a dash by some readers, and no one should have to infer direction from a hue.

**The Warm Neutral Rule.** Every neutral in this system carries hue 75–88. A pure grey, a pure black, or a blue-grey neutral breaks the paper illusion and makes the page feel like a generic admin panel.

## Typography

**Display Font:** Iowan Old Style (with Palatino Linotype, Palatino, Book Antiqua, Georgia, Times New Roman)
**Body Font:** the platform UI sans (ui-sans-serif, system-ui, Segoe UI, Roboto)
**Label/Mono Font:** the platform monospace (ui-monospace, SFMono-Regular, SF Mono, Menlo)

**Character:** A serif that behaves like a printed statement against a sans that disappears. The serif names things — page titles, panel headings, the wordmark — so that the structure of the document is legible before a single word is read. The sans carries prose and labels and is never asked to be interesting. The monospace carries money, and only money; it is never used as a costume for "technical".

### Hierarchy

- **Display** (400, 1.875rem, 1.25, −0.025em): the page title only. One per screen, set in the serif at `leading-tight`.
- **Title** (400, 1.125rem, tracking −0.025em): a Panel's own heading, in the serif. The second level of naming.
- **Body** (400, 0.875rem, 1.625): prose and descriptions at a generous leading. Hold the measure to roughly 65–75 characters; `max-w-2xl` on the page header is what enforces it today.
- **Label** (600, 0.6875rem, 0.14em, uppercase): the eyebrow — field labels, notice tone names, navigation, status chips, and the masthead's `Ledger` sub-label. It names a real thing and nothing more. Small, spaced, and quiet.
- **Amount** (400, 0.875rem, monospace, tabular figures): every monetary value. Right-aligned in a column.

### Named Rules

**The Serif Names Rule.** The serif is used for things that *have names* — page titles, panel headings, the wordmark. Prose, labels and controls are sans. A paragraph set in the serif is a mistake, not a choice.

**The Mono Means Money Rule.** Monospace is reserved for figures that must align or be compared: amounts, dates in columns, quantities. It is never used to imply technicality, and never applied to running text.

**The No Kicker Rule.** The page header carries no eyebrow above its `<h1>`. A kicker that only restates the heading's own words puts a second, quieter voice on the page, and the reader's eye lands on the quiet one. Anything worth labelling belongs in the body as data — a definition row, a field label, a status chip. This is a deliberate omission, not an unfinished one.

**Documented deviation — the display face is a system stack.** The serif is `Iowan Old Style` → `Palatino Linotype` → `Palatino` → `Book Antiqua` → `Georgia`, resolved from the platform rather than downloaded. This is a knowing exception to the usual rule that an own-world display face must be sourced and self-hosted, and it is deliberate on three counts: the app is designed to run with the network switched off as a proof of no telemetry, nothing in the UI is fetched at runtime, and the licence policy for bundled assets is still unresolved. The chain lands on a face with real character on every target platform, so nothing degrades. Revisit only if all three of those change.

## Layout

A single centred column, `max-w-5xl` (64rem / 1024px), padded `1.5rem` on both sides, holding every route. There is no multi-column dashboard grid and no sidebar: the route table is flat, each page renders its own `AppShell`, and the reader is always reading a document that is one column wide.

Above the fold the masthead is a band of Paper at 85% opacity with a `backdrop-blur`, underlined by a 1px Rule, containing the wordmark in the display serif and navigation in Label. Below it, `main` opens with 2.5rem of top space and closes with 6rem of bottom space — the deep bottom margin is deliberate, so the last row of a long ledger never sits against the viewport edge.

Vertical rhythm is tight within a group and generous between groups. A page header clears its content by 2rem; a Panel header clears its body by 0.875rem. Data rows are a three-track grid — `auto` for the date, `minmax(0, 1fr)` for the description, `auto` for the amount — at `0.875rem` block padding and `1rem` inline gap, separated by hairline dividers rather than by individual row borders or card chrome.

Responsive behaviour is mobile-first via Tailwind's default breakpoints: the masthead wraps (`flex-wrap` with `gap-y-2`), navigation wraps onto a second line rather than collapsing into a menu, and the container simply stops growing at 64rem. There is no mobile-specific navigation treatment.

## Elevation & Depth

This system is **flat, with a paper-edge whisper**. Surfaces do not float above one another; they are stacked sheets of the same paper, separated by a 1px Rule. The one shadow in the entire system belongs to the Panel.

### Shadow Vocabulary

- **Paper Edge** (`0 1px 0 0 oklch(0 0 0 / 0.02), 0 1px 3px 0 oklch(0.2 0.02 80 / 0.05)`): the cut edge of a sheet catching light, plus a 3px settle. Applied to `Panel` and nothing else. It is near-invisible by design — if you can clearly see it, it is too strong.

### Named Rules

**The Hairline Rule.** Separate two things with a 1px Rule, not with a shadow. A shadow that lifts a surface off the page breaks the paper illusion immediately, and is the single fastest way to make this system look like a generic admin panel.

**The One Shadow Rule.** Panel is the only element permitted a shadow. Buttons, fields, list rows, chips and notices are flat. Do not add a second elevation tier.

## Shapes

Gently curved, never rounded. The base radius is `0.375rem` (6px), stepping to `0.25rem` for chips and badge corners, `0.4375rem` for panels, and `0.75rem` only where a container needs to read as a distinct sheet. Nothing is a pill; the most curved thing in the system is the 6px base.

Form language is *ruled paper*. Structure comes from hairlines and generous space, not from filled or outlined boxes. A list is one continuous surface divided by 1px rules — never a stack of individually bordered rows. Fields are the only routinely outlined elements, and their stroke is Rule Strong rather than Rule so an input is distinguishable from a divider without being louder.

## Components

Tactile and precise: ink pressing into paper. Controls feel like a stamp, money columns lock, and nothing bounces, glows or blooms.

### Buttons

- **Shape:** gently curved (0.375rem radius), medium weight, 1px border, `0.5rem 0.75rem` padding.
- **Primary:** Ledger Ink fill on Paper text. Used once per view — the one action this page exists to perform.
- **Hover / Focus:** Primary darkens to 90% opacity on hover with a 150ms colour transition. `:focus-visible` draws a 2px Focus Ink ring at 2px offset — deliberately not the browser default, which disappears against Rule on a paper ground. Disabled drops to 45% opacity and stops receiving events.
- **Quiet:** Paper fill, Rule border, Ink text. The default. Hover moves to Paper Tint.
- **Danger:** Paper fill, 40%-strength Alarm border, Alarm text. Hover tints to 10% Alarm. Reserved for destructive actions only.

### Chips

- **Style:** Paper Tint fill, Rule border, `0.3rem` radius, Label typography at 0.6875rem with 0.12em tracking.
- **State:** status chips are informational and never clickable. Filter and selection chips use the segmented control below instead.

### Segmented Control

A `0.5rem`-radius Paper Tint track holding `0.25rem`-radius Paper segments, used for mutually exclusive filters. The active segment is Paper with full-strength Ink text; inactive segments are Ink Soft. This is the only filled-on-filled control in the system, and it exists because a row of separate pills would be louder than a filter should be.

### Cards / Containers

- **Corner Style:** 0.4375rem.
- **Background:** Paper, identical to the page ground. A Panel is defined by its border and its header rule, not by a different fill.
- **Shadow Strategy:** Paper Edge only — see Elevation.
- **Border:** 1px Rule.
- **Internal Padding:** `1.25rem` inline on rows, `0.875rem` block; headers use `1.25rem 0.875rem` under a 1px bottom Rule.

### Inputs / Fields

- **Style:** 1px Rule Strong stroke, Paper fill, `0.5rem 0.625rem` padding, `0.875rem` text.
- **Focus:** a 2px Focus Ink ring at 2px offset, matching the button focus treatment exactly.
- **Error / Disabled:** errors appear as a `0.75rem` destructive message 0.375rem below the field — the field's own stroke does not turn red, because the message already says what is wrong. Disabled fields drop to Paper Tint with a `not-allowed` cursor.
- **Labels:** every field carries a Label eyebrow above it, and a hint or an error below — never both.

### Notices

Tone-framed messages for error, warning and information. Each carries a 1px frame in its tone colour at 35% strength over a 4.5% strength tint of that same colour, with a Label eyebrow naming the tone. Tone is therefore carried three times over — tint, label, frame — so the message survives a monochrome print and a screen reader. The frame is a hairline like every other rule in the system; no notice gets a heavier bar down one side.

### Browser Surfaces

The parts the browser draws for you carry the design too, because they are the cheapest signal that a page was built rather than assembled.

- **Selection:** a warm 18% wash of the theme's own ink (`--selection`), with the text left at `--foreground` on top. In the dark theme the wash inverts to near-white at 22%, because `--foreground` there is already near-white. It must be spelled as an explicit alpha rather than `color-mix(… transparent)`: the build rewrites `color-mix` into an `@supports` block and degrades the fallback to the *opaque* colour, which would put ink text on an ink ground.
- **Caret:** Ledger Ink in the light world — the one accent, spent on the one thing being typed into. In the dark theme `--primary` is already inverted to paper, so the same declaration stays legible without a second token.
- **Scrollbars:** thin, with a `--border` thumb on a transparent track, lifting to `--input` on hover, at the system's own `0.375rem` radius. A rule-coloured thumb reads as paper; a grey chrome bar reads as an operating-system overlay sitting on top of the document.

### Arrival

One authored moment, and only one: everything arriving on a page rises and settles, so a list that lands all at once reads as a flash instead of a document. Nothing animates on scroll — a page that moves when you scroll past it stops being a document you are reading — and there is no second animation competing with it.

What varies is **amplitude within that one moment**, not choreography. The same keyframes and easing serve two jobs that are not the same job: `.rise` is the page's own arrival, one per route, travelling 8px over 520ms; `.rise-row` is a list entry, of which there may be two hundred, travelling 4px over 380ms with a per-index delay set from JS and capped there. The loudest entrance in the system is calibrated for the page heading and is allowed exactly one.

Travel stays on the Y axis and fade on opacity, on purpose. A scale, a blur or a colour shift on arrival would read as something being *presented* rather than a sheet being laid down. `prefers-reduced-motion: reduce` drops both amplitudes to `animation: none` entirely — a reader who asked for no motion gets none.

### Navigation

The masthead wordmark is set in the display serif at 1.125rem, followed by a `Ledger` sub-label in Label typography with wider tracking. Navigation links are Label typography: Ink Soft at rest, Ink on hover. **The active item carries a 2px Ledger Ink underline** (`::after`, inset-x 0, at −1px, height 2px) rather than a filled pill, a background shift, or a weight change. Links have no default underline; add `underline-offset` deliberately when one is needed.

### Amount

The signature component, and the one most worth protecting. Monospace with `tabular-nums`, right-aligned, whitespace-nowrap, so a column of amounts aligns on the decimal point and a flipped sign is impossible to miss in a dense list. Direction is the sign glyph first, then Money Out / Money In / Ink Soft for zero. The currency code sits at `0.75em` with `0.7` opacity — present but subordinate. An `sr-only` span states "money in" or "money out" in words for screen readers.

### List Row

A three-track grid at `0.875rem` block padding, divided by Rule, hovering to Paper Tint at 70%. The left track holds the date in Label typography, the middle track the description in Body with the amount in the right track. Rows are not individually bordered and are never cards.

### Empty State & Skeleton

Empty states are centred, with a serif `1.125rem` title, muted prose at `max-w-md`, and an optional action at 1.25rem. They state honestly what is absent — LifeOS prefers saying "no budgets defined" to drawing an illustration of an empty budget. Skeletons are shaped like the thing being loaded: rows of hairline Paper Tint bars at the real row rhythm, not the word "Loading". The container is `role="status"` with an `aria-label` naming what is loading, because the live region takes no name from its contents.

## Do's and Don'ts

### Do:

- **Do** carry money direction with the sign glyph first and colour second, always.
- **Do** set every amount in tabular monospace and right-align it, so columns align on the decimal.
- **Do** separate surfaces with a 1px Rule; reach for the Paper Edge whisper only on `Panel`.
- **Do** spend Ledger Ink only on things the reader can act on.
- **Do** let the serif name things and the sans carry prose — never swap them.
- **Do** honour `prefers-reduced-motion` by dropping the animation entirely, not by shortening it.
- **Do** keep text selection, the caret, and scrollbars on the palette's own terms. They are the cheapest signal that a page was built rather than assembled, and the first thing that regresses when nobody is looking.
- **Do** state honestly when a surface has no data behind it, the way the empty states already do.
- **Do** keep every asset local: no runtime-fetched font, icon, or stylesheet. The app is designed to run with `--network=none` as a proof of no telemetry, and that is a product guarantee, not an accident.

### Don't:

- **Don't** add a charting library, or design a chart, before an aggregation endpoint exists to feed it. Net-worth-over-time, spend-by-category and cashflow-by-period have no endpoint today; the arithmetic is correct but nothing exposes a series.
- **Don't** put a chart on a page whose data comes from a stub endpoint. `/review`, `/imports` and `/budgets` currently have nothing behind them.
- **Don't** introduce hard offset shadows, gradient text, glass or backdrop-blur as decoration. The only blur in the system is the masthead's, and it is doing a job.
- **Don't** let colour be the sole carrier of meaning anywhere on the page.
- **Don't** wrap a list in individually bordered rows or nest cards inside panels. One surface, hairline dividers.
- **Don't** use colour alone to distinguish a filter chip's active and inactive states; the fill carries it.
- **Don't** silently replace the system font stacks with a downloaded webfont without revisiting the offline guarantee first.
- **Don't** invent numbers to fill an empty view. Empty states say what is missing.
