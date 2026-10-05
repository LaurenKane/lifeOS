# Surface brief — Overview (`/`)

## Scope and mode

Route `/` in the LifeOS React + Vite app. Mode: **Operate**. The visitor completes a task —
they arrive to read what their money did and to find the one thing that needs their attention.

This is the first surface of a **replacement visual world**. The incumbent "ledger paper"
world (warm cream ground, Palatino display serif, hairline rules) is evidence of the product,
not authority over what it becomes. DESIGN.md is rewritten at finish from the built world.

## Audience, job, proof

One user. Technically competent, does not want to be walked through anything. He is checking
last month's spending *during* the month, and once a month he imports statements and clears a
review queue.

Job: **answer any question about my own money.** Success is a number he can state without
hedging. Not a dashboard that impresses him.

Proof on this surface: the net-worth figure, the period's spend, and the open review count —
each traceable to a statement he can open.

## Constraints (durable, from PRODUCT.md and ARCHITECTURE.md)

- Amounts are **integer EUR cents**. Never a float on screen. Transaction columns need figures
  that align vertically — a ledger that cannot be scanned down a column is broken. This is a
  functional requirement and outranks the reference set, which sets currency in proportional
  figures with no monospace anywhere.
- **No telemetry.** Nothing fetched at runtime. A downloaded font is permitted only if the user
  chooses it after seeing both options rendered; this is unresolved and is why the page ships a
  runtime type toggle.
- **Errors are loud, empty views are honest.** There is no seed data. Empty is a real state and
  must read as a real state, not as a mock.
- Colour is never the sole carrier of meaning. Any chip or state carries a shape or a label too.
- Must work at 390px and at 1440px. He will use it on his phone.

## Chosen direction

**The Dutch payment slip** (`bankgier`) — candidate 3 of the grounded list, the direction the
roll assigned. Not the literal printed artefact: its *grammar*. A bankgier is a form whose
whole purpose is to get one number to be trusted, and it gets there with boxed fields, a
prominent amount box, and a perforated tear-off stub for the part you keep.

The thing LifeOS does that no bankgier does: the amount box shows `assets − liabilities` across
accounts a bank treats as separate worlds, and every figure in it traces to a raw statement
record. That is the mechanism this surface has to prove, and it is why the box is the loudest
thing on the page.

### World

Ground is a warm off-white that is green enough to sit *under* an acid lime rather than fight it.
Exactly two saturated brand colours, lime and pale cyan, and they carry equal rank — the user
corrected the reference set on this: two main colours, not one. Ink is softened near-black, never
pure black, so the lime and cyan sit against something warm.

**Flatness is load-bearing.** No shadows, no borders, no glass. Colour fills do all the
separating. This matters more here than in the reference: flattening the corner radius removes
one softness cue, and the reflex is to add a shadow back, which would undo the flatness. Do not.
If a surface needs to separate from its neighbour, it gets a different fill.

Radius is a **single small absolute token**. Measured on the references: 8–12% of a card's own
width on in-app cards, and pills at 50% of height. The user rejected both. Cards land at 6px,
controls at 6px, and only genuinely circular things — the account avatar, the progress ring —
are round.

Type is a **neutral grotesque**: double-storey `a`, closed `g` descender, `o` and `i` dots
slightly narrow rather than perfect circles, flat horizontal terminal cuts. The reference face is
Urbanist, a geometric monolinear sans whose stroke contrast measures 1.0 — so "curly" was never
about stroke contrast, it was skeleton and aperture. Weight range is tight; there is no serif
anywhere and no decorative face.

### First viewport

The masthead carries the wordmark and section navigation on one line, no blur, no band. Beneath
it the **spending panel** — the one lime-filled region on the page, sized by its own content and
never stretched to match a neighbour. It holds, in order: the period, the amount spent in it, the
comparison against the previous period, and the top category that consumed it. Nothing else on the
page is allowed to be that loud.

The **net-worth figure** is the second panel, not the first. It still reads `assets − liabilities`
across accounts a bank treats as separate worlds, and it is still traceable to raw statement
records — that remains the mechanism this surface proves. It simply is not the headline yet,
because with no savings or investment holdings it has less to say than the spending figure does.

To the right on desktop, stacked: the net-worth panel, and the review stub whose fill **is** its
state — cyan when the queue is empty, lime when there is something to clear. The tear-off stub: a
short panel whose top edge is perforated, holding the open review count. One perforation on the
page, not a motif.

**No "Hello Robert".** The user called that wasted space, correctly — an introduction that
restates the page's own title is a header wearing a greeting's clothes. The first thing on screen
is a number.

The user also called the reference cards too large, and specifically called the net-worth card too
big on desktop. Cards are content-height, not fixed-height, and **no panel may be stretched to
match the height of its neighbour** — unequal column heights are the correct outcome. The grid
should be `items-start`.

### Form

Structure: one asymmetric two-column grid on desktop, one column on mobile. The amount box spans
the full measure; panels are their content's height. Baseline rhythm on a 4px unit, 8px gutters
inside panels, 24px between panels.

Signature interaction: **the perforation**. The review panel's fill transitions between cyan and
lime as the queue empties and fills, and its top edge keeps its perforated profile through both
states, so the shape tells you it is a stub before the colour tells you anything. Under
`prefers-reduced-motion` the fill changes instantly and the perforation stays.

### Cross-surface reach

The amount box, the two brand fills, the single radius token and the grotesque hold for every
surface that follows. The perforation does not — it is reserved for panels that represent a
detachable, dismissible thing (a review stub, an import stub), and nowhere else.

### Honest risk

The payment-slip grammar is at its weakest when a panel has no counterpart on the printed form —
a net-worth series has no bankgier equivalent, and forcing one is exactly where this direction
could become a costume. The mitigation is that the box grammar earns its keep only where a form
field is genuinely being reported; where it is not, the panel is a plain fill on the ground.

## Resolved decisions

1. **Prose face: Work Sans, self-hosted.** Chosen by the user after seeing both candidates
   rendered on the real page at 390 and 1440. Self-hosting from the app's own origin satisfies
   the `--network=none` guarantee — the font is served by the app, not a CDN — so the craft floor's
   ban on a system display face and the offline guarantee are both satisfied. Its OFL text ships in
   `frontend/public/fonts/OFL-WorkSans.txt`, which is what `LifeOS-1km` needs.
   **Why it won:** x/cap 0.52 reads lower and wider, and it does not crowd the small labels.
   Inter's larger x-height would have competed with a page that already carries a mono figure
   face; Work Sans leaves the numbers to do that work.
   **Figures stay in JetBrains Mono** and do not move with the prose face. That was never a
   variable in this decision and never should be.

2. **The page leads with spending, not net worth.** Corrected by the user on 2026-10-05 after
   reviewing the build. The original direction put `assets − liabilities` in the loudest panel,
   which is an M10 idea front-running V1: PRODUCT.md records V1 scope as spending, and with no
   savings or investment holdings there is nothing meaningful for a net-worth figure to say yet.
   Leading with net worth also buried the thing he actually comes to the page for — "what did I
   spend last month" — which PRODUCT.md names as the mid-month use.

   **This reorders the first viewport and nothing else.** The panels, the palette, the radius
   token and the grotesque are unchanged; the lime panel becomes the spending panel and the
   net-worth figure becomes the second panel. When savings and investments land, net worth is the
   natural thing to promote back to first, and the structure should make that a content decision
   rather than a rebuild.

   Recorded because it is the second time an assumption about this product's centre of gravity was
   wrong, and both times only asking surfaced it.

## Direction contract

**THESIS:** A payment slip earns trust by putting one number in a box and making everything else
a field. This page refuses the reference set's greeting-and-avatar opening — it opens on the
number, because the number is the product. And it refuses to open on the wrong number: V1 is
spending, so spending is the box, and net worth waits its turn.

**OWN-WORLD:** Warm green-tinged off-white ground. Two saturated fills of equal rank: acid lime
`#E7FE54` and pale cyan `#C0E7EC`, each used as a whole panel and never as a hairline or a 1px
rule. Softened near-black `#282828` ink, warm grey for secondary. Panels are flat: no shadow, no
border. One radius token, 6px, on cards and controls alike; circles only where the shape is
genuinely a circle. **Work Sans throughout** — self-hosted, x/cap 0.52, double-storey `a`, closed
`g` descender, slightly narrow apertures, flat terminals — with **JetBrains Mono for every
figure**, so amount columns align. No serif anywhere, and no runtime font fetch.

**STORY:** The visitor reads what they spent last month and how that compares, then reads what
they are worth and how that came to be. They see exactly one thing asking for a decision. They
learn that the figure is assembled from accounts a bank would show separately, and that every
number is traceable.

**FIRST VIEWPORT:** Masthead, one line, wordmark left, sections right. Below, full measure: the
lime spending panel — the period, the amount spent, the comparison against the previous period,
the top category. Sized by its content; the grid is `items-start` and no panel stretches to match
its neighbour. The net-worth figure follows as the second panel, reading `assets − liabilities`.
Desktop puts the review stub in the right column; mobile collapses to one column with the
spending panel first and unchanged in scale. No greeting, no avatar row, no intro copy.

**FORM:** Asymmetric two-column grid desktop / single column mobile, content-height panels on a
4px rhythm. Position 3 of 7 grounded candidates; seed key `2625d264`. The four catalog
challengers were declined: each contradicted the pinned two-colour, straight-type brief.

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review,
the verdict, DESIGN.md, and every shipping raster carrying its provenance