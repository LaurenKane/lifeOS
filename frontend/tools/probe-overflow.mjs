/**
 * Overflow probe — throws the real page at 390px and 1440px and reports anything
 * wider than its container.
 *
 * This is a measurement harness, not a test: it needs a layout engine, so it runs
 * under Playwright rather than jsdom. What it checks is the one thing a unit test
 * cannot see — whether the widest figure on the page fits the narrowest column it
 * has to fit in.
 */
/* See tools/README.md: playwright is installed out of tree, never as a project
 * dependency, because `package-lock.json` is gated on an approved ledger. */
import { chromium } from "playwright";
import { existsSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:5199";
const WIDTHS = [390, 1440];
/* Demo and real-empty are the two states the page can actually be in, and both are
 * measured: the demo fills every panel and the real empty ledger fills none of
 * them, so the two stress the layout in opposite directions. */
const STATES = [
  { path: "/?demo=1", label: "demo" },
  { path: "/", label: "real-empty" },
];

/** The cached headless shell, which is usually a different revision than the
 * installed playwright expects. Pointing at it directly is cheaper than
 * downloading another 150 MB for a one-off measurement. */
const SHELL =
  process.env.SHELL ??
  `${process.env.HOME}/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell`;
const browser = await chromium.launch(
  existsSync(SHELL) ? { executablePath: SHELL } : {},
);
let failures = 0;

for (const { path, label } of STATES) {
  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();

    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);

    const report = await page.evaluate(() => {
      const doc = document.documentElement;
      const overflowing = [];
      for (const el of document.querySelectorAll("*")) {
        const parent = el.parentElement;
        if (parent === null) continue;
        // An element wider than its parent's content box, by more than a pixel.
        if (el.scrollWidth > parent.clientWidth + 1) {
          overflowing.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className ?? "").toString().slice(0, 90),
            scrollWidth: el.scrollWidth,
            parentWidth: parent.clientWidth,
            text: (el.textContent ?? "").trim().slice(0, 60),
          });
        }
      }
      return {
        docScroll: doc.scrollWidth,
        docClient: doc.clientWidth,
        bodyScroll: document.body.scrollWidth,
        overflowing: overflowing.slice(0, 12),
        figure: document.querySelector(".font-mono")?.textContent ?? "",
      };
    });

    const horizontal = report.docScroll > report.docClient + 1;
    const bad = horizontal || report.overflowing.length > 0;
    if (bad) failures += 1;

    console.log(
      `${bad ? "FAIL" : "ok  "} ${label.padEnd(11)} ${String(width).padStart(4)}px ` +
        `doc ${report.docScroll}/${report.docClient}` +
        (report.overflowing.length > 0 ? `  overflowing:${report.overflowing.length}` : ""),
    );
    for (const o of report.overflowing) {
      console.log(`        <${o.tag} class="${o.cls}"> ${o.scrollWidth} > ${o.parentWidth}  "${o.text}"`);
    }

    await context.close();
  }
}