# ADR 0008: MIT for Life OS, and no AGPL code in the tree

**Date:** 2026-10-05
**Status:** Accepted
**Decision Drivers:** the discovery phase nearly recommended forking an AGPL project; the user may
later publish Life OS or make it public; and owning the domain model with no copyleft is what
preserves that option. This ADR exists so that dead end is not re-derived.

## Context

Four projects were examined as references during discovery. Three are AGPL-3.0. One is MIT. The
distinction is not academic: AGPL's copyleft reaches a *network service*, so adopting AGPL code
would constrain a future hosted Life OS in a way MIT never would. That asymmetry — not code
quality — is what decides this.

The licences below were **verified against each project's own licence file or manifest on
2026-10-05**, not recalled. Two corrections to the original working notes are recorded in
[Corrections](#corrections-to-the-original-notes) because both are the kind of error a later agent
would otherwise reproduce.

### The three AGPL projects

**BankingSync** — AGPL-3.0, `LICENSE` is stock AGPLv3 text.
<https://github.com/RomanSpies/BankingSync>. Also confirmed: it is **Go** (`go.mod`: `module
bankingsync`, `go 1.25.0`; 144 `.go` files) and it **carries its own SQLite database**
(`store/store.go`: `const DBPath = "/data/bankingsync.db"`, `sql.Open("sqlite", ...)`; README
"SQLite for storage"). Both traits confirmed because they are the two reasons a fork was
tempting — a working sync engine, and a persistence layer that does not require the Postgres this
project is built on.

**Firefly III** — `AGPL-3.0-or-later`. The authoritative string is in `composer.json`
(`"license": "AGPL-3.0-or-later"`); the `LICENSE` and `COPYING` files are stock AGPLv3 text with
no suffix. The `-or-later` suffix matters: it is what lets a downstream licensee elect a future
AGPL version, so writing a bare "AGPL-3.0" understates the grant. Note that GitHub's API and most
licence-scanning tooling report bare `AGPL-3.0` here, because they read the licence *text* and
cannot see a suffix that lives only in the manifest. A scanner reporting `AGPL-3.0` is not wrong.

**Wealthfolio** — AGPL-3.0 for code, at current release v3.9.1. The repository **moved**:
`afadil/wealthfolio` → `wealthfolio/wealthfolio` (<https://github.com/wealthfolio/wealthfolio>);
the old path now redirects. Two details are recorded because each is a trap:

- **Anything tagged before 2024-10-15 was LGPL-3.0, not AGPL.** The licence history is two commits:
  `ba981a43` (2024-05-27) added LGPL-3.0, and `70f6d2cb` (2024-10-15, "update licence") replaced
  it with AGPL-3.0. An agent consulting an old tag or an old blog post would reach the opposite
  conclusion.
- **`assets/brand/` is not AGPL at all.** Its README states the assets "are not licensed under the
  project's software license (AGPL-3.0). No trademark rights are granted." `TRADEMARKS.md` requires
  forks to remove or replace the brand assets and not imply official status. AGPL grants no
  trademark rights, so "AGPL-3.0" overstates what a fork may actually take.

There is also a `CLA.md` granting Teymz Inc. the right to relicense inbound contributions under
proprietary terms. That governs contributions *to* Wealthfolio and imposes nothing on a downstream
consumer; it is noted only so it is not mistaken for a restriction on us.

### Actual Budget is MIT — with an attribution condition

**Actual Budget** — MIT. `LICENSE.txt` carries "Copyright James Long"; root `package.json` declares
`"license": "MIT"`. <https://github.com/actualbudget/actual> (branch `master`, commit `25d49dc4`).
A recursive scan of the tree for `LICENSE`/`LICENCE`/`COPYING`/`CLA`/`TRADEMARK`/`NOTICE`/`PATENTS`
found only the root `LICENSE.txt` and `packages/sync-server/LICENSE` (also MIT). **No CLA, no DCO
sign-off, no trademark policy.** The licence has never been changed.

So its patterns may be used, and using them does not oblige us to publish anything.

## Decision

1. **Life OS is licensed MIT.** Permissive, no copyleft, no network-service clause.

2. **We do not fork, embed, or vendor code from BankingSync, Firefly III, or Wealthfolio.** All
   three are AGPL-3.0 (`-or-later` for Firefly III). Reading them for design is not copying; the
   line is drawn at whether any of their source enters this tree, directly or via a patch.

3. **Actual Budget may be used as a reference, and its code may be copied — carrying the notice.**
   MIT permits reuse in a proprietary work with no share-alike obligation, **provided the
   copyright notice and licence text are reproduced**. Any copied Actual Budget code must ship
   with James Long's copyright line and the MIT text, recorded in the file it came from. This is
   the one place where copying is permitted, and the condition is not optional.

4. **Behaviour and ideas are not copyrightable; code is.** Describing a *behaviour* another project
   has is always permitted and needs no attribution. Where this tree already does that, the comment
   says "behaviour" and not "code" — deliberately, so a later reader does not mistake a described
   behaviour for a copied implementation.

5. **The AGPL door stays open on purpose.** If the user publishes Life OS, MIT means that choice is
   genuinely available. Owning the domain model with no copyleft is what preserves it. This is the
   reason for the decision, not a side effect of it.

## Consequences

- Discovery must not recommend an AGPL fork again. This ADR is the answer to that question, and a
  future agent re-deriving it has failed to read this file.
- A test comment in `backend/finance/tests/integration/test_pipeline.py` referenced BankingSync's
  `MergePatch` **behaviour** as "worth copying". Under decision 4 that was always legitimate, but
  the wording invited reading it as copied code from an AGPL project. It now cites this ADR and says
  "behaviour" explicitly.
- The repository currently satisfies decisions 1–2 with room to spare. Verified 2026-10-05: zero Go
  files; no SQLite anywhere (this project is Postgres-only, so BankingSync's persistence layer was
  never a candidate); no `vendor/`, `third_party/`, or fork directories; and of 46 installed Python
  distributions, **none carries a copyleft licence classifier**. The frontend depends on React,
  React Router, and Zod plus build tooling, with no AGPL or GPL text in any installed licence file.
- **Recording the policy is not the same as being licensed.** When this ADR was first written there was
  no `LICENSE` file at the repository root and no `license` field in either manifest, so the tree was
  not formally MIT-licensed to a third party — only an intention. That gap is now closed
  (LifeOS-1km): `LICENSE` at the root carries verbatim MIT text, "Copyright (c) 2026 Lauren Kane",
  and both `pyproject.toml` and `frontend/package.json` declare the licence so it travels with built
  artefacts. `pyproject.toml` uses PEP 639 (`license = "MIT"` plus `license-files`) so wheel metadata
  carries a machine-readable `License-Expression: MIT` *and* the bundled text; the older
  `license = { file = "LICENSE" }` form was tried first and rejected, because it copies the file body
  into `License:` and yields the unparseable value `MIT License`.
- The copyright holder is a real input rather than something the repository can infer, which is why
  this was a separate step. Had the ADR alone been treated as licensing the project, the gap would
  have stayed open indefinitely and looked closed.
- Third-party licences are a moving target. Any *new* AGPL-licensed dependency is a decision, not
  an accident, and needs this ADR amended first.

## Corrections to the original notes

The working notes this ADR replaces contained two errors. Both are recorded because a wrong licence
string is worse than a missing one.

1. **"Actual Budget is MIT, so its patterns may be used directly with no attribution requirement" is
   wrong.** MIT *has* an attribution requirement: the licence obliges that "the above copyright
   notice and this permission notice shall be included in all copies or substantial portions". What
   MIT removes is the copyleft obligation, not the notice. Had the looser phrasing been recorded, a
   future agent could have copied Actual Budget code and shipped without the notice — a licence
   breach reached by following our own ADR. Decision 3 above states the condition.
2. **"Firefly III is AGPL-3.0" understates the grant.** The identifier is `AGPL-3.0-or-later`.
   See above for why tooling will report the bare form.

A third note was corrected in the project's favour and is worth keeping: Wealthfolio's
pre-2024-10-15 tags are LGPL-3.0, so an agent reasoning from an old tag would wrongly conclude the
AGPL constraint never applied.