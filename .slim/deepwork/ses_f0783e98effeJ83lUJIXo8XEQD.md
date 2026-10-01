# Deepwork — LifeOS-5 (M0) resume

## Goal

Finish bead LifeOS-5 (M0: repo skeleton, Docker Compose, invariants, CI) on branch
`feat-add-m0-project-foundation-with-invariants-c`, including the three split beads
created when the first attempt was halted.

## Repo / branch

- Repo: `/home/lauren/Documents/NewCode/LifeOS`
- Branch: `feat-add-m0-project-foundation-with-invariants-c` (4 commits ahead of main)
- Sibling worktree `LifeOS-lif-2` on branch `lif-2` belongs to another agent (bead
  LifeOS-2, Amex PDF exports). DO NOT touch it.

## Where we resumed

Already complete and verified:

- `ARCHITECTURE.md` — 154 lines, tree checked line-by-line against
  `docs/ARCHITECTURE-PROPOSAL.md` section D.
- Invariant engine — `scripts/check_invariants.py`, `invariants.yaml`,
  `scripts/migrations.lock.json`, `tests/invariants/`. **30/30 negative tests pass.**
  Committed checker is byte-identical to the orchestrator's verified copy.
  Exit codes strictly 0 pass / 1 violation / 2 config error.

Two real logic bugs in the invariant *definitions* were found and fixed during the
first attempt. Both would have shipped a green CI that caught nothing:

1. `raw_data_immutable` used a negative lookahead, so it ignored
   `UPDATE source_record SET raw_data = ...` (the exact violation) while flagging
   the legal `UPDATE ... SET status = 'booked'`.
2. `no_cross_schema_fk` missed quoted schemas: in `REFERENCES "health".account(id)`
   the closing quote sits *after* the schema name.

## Phases

- [x] P0 Recon — state verified against disk (this file)
- [ ] P1 Backend package repair — bead LifeOS-by8 — GATE: ruff, mypy --strict,
      lint-imports, pytest backend/tests all green on real output
- [x] P2 Frontend build + test repair — bead LifeOS-kv0 — **VERIFIED GREEN by the
      orchestrator, independently of the agent's report:**
      `npm run build` exit 0 (133 modules, dist CSS 8.24 kB, JS 399.97 kB);
      `npx tsc -b --force --noEmit` exit 0; `npm test` 15 passed / 2 files;
      `npm run lint` exit 0 (only when redirected to a file — piping its output
      makes the harness report a false failure); preview served 200 on :4173.

      Root causes were 3 defects repeated across 4 feature modules, plus 3 more
      the brief did not know about: the app never mounted (no `createRoot`),
      Tailwind emitted ZERO css (v3 `tailwind: true` + `@tailwind base` under v4,
      and `index.css` was imported only by a dead duplicate file), and
      `apiClient.ts` threw at module init (`z.string().url()` rejected its own
      `/api` default). Money is now `amountMinor: z.number().int()` everywhere,
      with a test asserting `10.5` is rejected and `1050` accepted — the no-floats
      invariant is machine-enforced, not merely documented.

      Integration facts: build = `npm run build` from `frontend/`; output dir
      `frontend/dist`; dev = `npm run dev` (:5173); test = `npm test`.
      Assets use absolute `/assets/...` paths, so the static server must serve
      `dist/` at root. `VITE_API_BASE_URL` is baked in at BUILD time, so a
      runtime-configurable API URL is a build arg, not a runtime env var.
- [ ] P3 Integration — bead LifeOS-pl7 — GATE: compose brings up healthy
      db/api/worker/frontend; CI green on fresh clone; each invariant has a
      negative test that fails CI
- [ ] P4 Pin fingerprint + final audit — GATE: full clean verification pass,
      no unverified claims

P1 and P2 touch disjoint paths and may run concurrently. P3 consumes both.
P4 last, because the pin must land after P1 settles `fingerprint.py`.

## Non-negotiables carried forward

- **Do not write Alembic migrations.** The schema, the deferrable balance
  trigger, and the `raw_data_immutable` DB trigger belong to M1 / bead LifeOS-6.
- `core/` is a SIBLING of `finance/`, not a parent.
- Money is signed BIGINT minor units, never floats.
- Zero-egress test is scoped to the offline file-import path; the M5 Enable
  Banking provider path legitimately makes live HTTPS calls.
- `fingerprint_frozen` is deliberately UNPINNED and reports exit 1. Pin it in P4
  so CI starts green — do not paper over it, an unpinned fingerprint is exactly
  where a silent edit goes undetected.

## Traps

- `npx tsc --noEmit` returns exit 0 while checking **nothing** — `tsconfig.json` is
  solution-style with `files: []` and references the app/node projects. Only
  `tsc -b` / `npm run build` is a real signal. This already produced one false
  "typechecks clean" claim in the first attempt.
- Never trust a "all checks pass" report without seeing the command output.
  Two of four lanes in the first attempt shipped broken or degenerate output.

## Safety

- NEVER `rm -rf` in this working tree. Use `python3 tools/rm_guard.py <path>`.
- Never delete a file not created in the current session without reading it.
- Never write credentials in this repo; secrets live in `~/.config/lifeos/`.
