# Measurement harnesses

Three scripts that check things a unit test cannot. They are not part of the app
and nothing in `src/` imports them; they exist so the claims in `src/index.css`
and `src/features/finance/overview/` can be re-verified rather than taken on
trust. Each needs a running `vite dev` and a browser, so they are deliberately
outside `npm test`.

    npm run dev -- --port 5199          # in one shell
    node tools/mock-api.mjs 8000        # in a second shell, answers every read empty
    node tools/probe-overflow.mjs       # or tools/audit.mjs

## `probe-overflow.mjs`

Loads the real page at **390px and 1440px**, in **both prose faces**, in both the
real and demo states, and reports any element wider than its parent. Eight
combinations in total.

This is the check that keeps the monospace figure honest. A seven-figure net worth
is thirteen glyphs of JetBrains Mono at 0.6em each, and the narrowest column it has
to fit inside is 302px on a phone — so the display size has to be derived from
where it sits rather than picked and hoped for. Switching the prose face must not
reflow the page either, which is why figures are set in the mono in both options:
if they moved typeface too, this probe would be measuring two changes at once.

## `audit.mjs`

Measures the craft floor on the rendered page:

1. **Contrast**, for every run of text against the actual painted background
   beneath it — including text on the lime and cyan fills, where the grey-on-ground
   token would fail. 4.5:1 for body, 3:1 for large text.
2. **Focus**, by tabbing through the page and asserting every stop carries a
   visible outline. An ink ring is the only ring that clears 3:1 against all four
   surfaces this app paints.
3. **Reduced motion**, by emulating `prefers-reduced-motion: reduce` and
   confirming the perforation is still on the element and that nothing is
   animating.
4. **Browser surfaces** — selection is an explicit alpha (never `color-mix()`,
   which Lightning CSS wraps in `@supports` and degrades the fallback to opaque),
   the caret is ink, and the radius is the one 6px token.

## `mock-api.mjs`

A stand-in for FastAPI that answers every read with `[]`. Not a test double — a
way of *seeing* the honest empty state, which is the one state this page most
needs inspecting and the one a working backend would hide. It never reaches the
database and it is never bundled.

## A note on `playwright`

These need `playwright`, which is **not** a dependency of this project and is not
in `package.json`. Installing it into the project's `node_modules` would change
`package-lock.json`, which CI gates on an approved-dependency ledger. Install it
out of tree and point `NODE_PATH` at it:

    mkdir -p /tmp/offscreen && cd /tmp/offscreen && npm init -y && npm i playwright
    cd frontend && NODE_PATH=/tmp/offscreen/node_modules node tools/audit.mjs

Each script also accepts `SHELL` pointing at a cached headless shell, since the
installed playwright's expected browser revision is often not the one on disk.