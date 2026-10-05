/**
 * Craft-floor audit, measured on the rendered page rather than asserted from the
 * stylesheet.
 *
 * Four things it checks, each of which is a rule a stylesheet cannot enforce on
 * its own:
 *   1. CONTRAST, for every run of text, against the ACTUAL painted background
 *      beneath it — including text sitting on the lime and cyan fills, where the
 *      grey-on-ground token would fail.
 *   2. FOCUS, by tabbing and checking that the focused control carries a visible
 *      outline on all four surfaces.
 *   3. REDUCED MOTION, by emulating the media query and confirming the perforation
 *      is still there and nothing is animating.
 *   4. SELECTION and the caret, which are browser-drawn and belong to no design
 *      system until somebody themes them.
 */
/* These need `playwright`, which is deliberately NOT a dependency of this project:
 * adding it would change `package-lock.json`, which CI gates on an approved ledger.
 * Install it out of tree and run with NODE_PATH pointing at it. See tools/README.md. */
import { chromium } from "playwright";
import { existsSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:5199";
const SHELL =
  process.env.SHELL ??
  `${process.env.HOME}/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell`;

const browser = await chromium.launch(existsSync(SHELL) ? { executablePath: SHELL } : {});
let failures = 0;
const fail = (message) => {
  failures += 1;
  console.log(`  FAIL ${message}`);
};

/* ── 1. Contrast, measured ──────────────────────────────────────────────── */
const CONTRAST = `
(() => {
  const srgb = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };
  const lum = (c) => 0.2126 * srgb(c.r) + 0.7152 * srgb(c.g) + 0.0722 * srgb(c.b);
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };

  const paintedBackground = (el) => {
    let node = el;
    let acc = null;
    while (node && node !== document.documentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0) {
        acc = acc === null ? bg : over(acc, bg);
        if (acc.a >= 0.999) return acc;
      }
      node = node.parentElement;
    }
    const root = parse(getComputedStyle(document.body).backgroundColor);
    return acc === null ? (root ?? { r: 255, g: 255, b: 255, a: 1 }) : over(acc, root ?? { r: 255, g: 255, b: 255, a: 1 });
  };

  const results = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let t = walker.nextNode(); t !== null; t = walker.nextNode()) {
    const text = t.textContent.trim();
    if (text.length < 2) continue;
    const el = t.parentElement;
    if (el === null) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") continue;
    if (el.closest(".sr-only")) continue;
    if (seen.has(el)) continue;
    seen.add(el);

    const size = parseFloat(style.fontSize);
    const weight = Number(style.fontWeight) || 400;
    // WCAG "large text": 24px, or 18.66px bold and up.
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const fg = parse(style.color);
    const bg = paintedBackground(el);
    if (fg === null) continue;
    const r = ratio(fg.a < 1 ? over(fg, bg) : fg, bg);
    results.push({
      text: text.slice(0, 44),
      ratio: Math.round(r * 100) / 100,
      need: large ? 3 : 4.5,
      size: Math.round(size),
      on: getComputedStyle(el.closest("section") ?? el).backgroundColor,
    });
  }
  return results;
})()
`;

for (const state of ["demo", "empty"]) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  await page.goto(`${BASE}${state === "demo" ? "/?demo=1" : "/"}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  const results = await page.evaluate(CONTRAST);

  console.log(`\ncontrast — ${state} (${results.length} runs)`);
  const bad = results.filter((r) => r.ratio < r.need);
  const worst = [...results].sort((a, b) => a.ratio - b.ratio).slice(0, 5);
  for (const w of worst) {
    console.log(`  ${w.ratio.toFixed(2)}:1 (needs ${w.need}) ${w.size}px on ${w.on}  "${w.text}"`);
  }
  if (bad.length > 0) fail(`${state}: ${bad.length} run(s) below the floor`);
  else console.log("  all runs clear their floor");
  await context.close();
}

/* ── 2. Focus ───────────────────────────────────────────────────────────── */
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  await page.goto(`${BASE}/?demo=1`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const ringed = [];
  for (let i = 0; i < 14; i += 1) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (el === null || el === document.body) return null;
      const s = getComputedStyle(el);
      return {
        tag: el.tagName.toLowerCase(),
        name: (el.textContent ?? "").trim().slice(0, 26),
        outline: s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0,
        colour: s.outlineColor,
        width: s.outlineWidth,
      };
    });
    if (info !== null) ringed.push(info);
  }
  console.log(`\nfocus — ${ringed.length} stops reached`);
  for (const r of ringed.slice(0, 6)) {
    console.log(`  ${r.tag} "${r.name}" outline=${r.outline ? `${r.width} ${r.colour}` : "NONE"}`);
    if (!r.outline) fail(`focus ring missing on ${r.tag} "${r.name}"`);
  }
  await context.close();
}

/* ── 3. Reduced motion ──────────────────────────────────────────────────── */
{
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1400 },
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await page.goto(`${BASE}/?demo=1`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  const report = await page.evaluate(() => {
    const stub = document.querySelector(".stub-edge");
    const animated = [...document.querySelectorAll("*")].filter((el) => {
      const s = getComputedStyle(el);
      return s.animationName !== "none" && parseFloat(s.animationDuration) > 0;
    });
    return {
      perforated: stub !== null && getComputedStyle(stub).maskImage !== "none",
      notchPitch: stub === null ? null : getComputedStyle(stub).getPropertyValue("--notch-pitch"),
      animatedCount: animated.length,
    };
  });
  console.log("\nreduced motion");
  console.log(`  perforation present: ${report.perforated} (pitch ${report.notchPitch?.trim()})`);
  console.log(`  elements animating: ${report.animatedCount}`);
  if (!report.perforated) fail("perforation disappeared under prefers-reduced-motion");
  if (report.animatedCount !== 0) fail(`${report.animatedCount} element(s) still animating`);
  await context.close();
}

/* ── 4. Browser surfaces ────────────────────────────────────────────────── */
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await context.newPage();
  await page.goto(`${BASE}/?demo=1`, { waitUntil: "networkidle" });
  const surfaces = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    return {
      selection: root.getPropertyValue("--selection").trim(),
      selectionHasAlpha: /^[^/]*\/|^rgba/.test(root.getPropertyValue("--selection").trim()),
      caret: getComputedStyle(document.body).caretColor,
      scrollbar: root.getPropertyValue("--rule").trim(),
      radius: root.getPropertyValue("--radius").trim(),
      /* The prose face is decided, so there is no attribute to read and no font to
         choose between. What can still be checked is that the one declared face
         actually resolved — a `@font-face` that failed to parse would fall back to
         the system grotesque silently and the page would look merely plain rather
         than broken. */
      declaredFaces: [...document.fonts].map((f) => `${f.family}${f.status}`).join(", "),
      prose: root.getPropertyValue("--font-prose").trim(),
    };
  });
  console.log("\nbrowser surfaces");
  for (const [k, v] of Object.entries(surfaces)) console.log(`  ${k}: ${v}`);
  /* `loaded` means the browser parsed the vendored woff2 and can draw with it. */
  if (!surfaces.declaredFaces.includes("LifeOS Sansloaded")) {
    fail("the declared prose face never loaded from the vendored woff2");
  }
  if (surfaces.declaredFaces.includes("LifeOS Sanst")) {
    fail("a second prose face is still declared on <html>");
  }
  if (!surfaces.selectionHasAlpha) fail("--selection is not an explicit alpha");
  await context.close();
}

await browser.close();
console.log(failures === 0 ? "\nCraft floor: all measured checks pass." : `\nCraft floor: ${failures} failure(s).`);
process.exit(failures === 0 ? 0 : 1);