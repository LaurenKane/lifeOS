# ADR 0012: the app root is the life Dashboard

**Date:** 2026-10-07
**Status:** Accepted
**Owner:** LaurenKane

## Context

The finance module shipped with its overview at `/`; ADR 0010 says a new
module gets route prefix `/<module>/*`. Discovery (Oct 2026) concluded the
first screen of the app must be life, with finance visible, not the other way
round: the user opens the app on a phone on a bad-brain day.

## Decision

**The Dashboard is the app root, served at `/`.** It composes, at the app
layer (ADR 0010, never cross-schema FKs or cross-module imports beyond
`public.py`): the Do now list, an Inbox-status line, Upkeep chips for today's
opportunities, a vision strip, and one or two finance summary cards (spend,
net worth) rendered from the finance module's existing read-only public
schema/API. The old `/` finance overview moves to `/finance/overview` (fits
the existing `/finance/*` prefix; nothing else in finance moves).

## Consequences

- The Dashboard is `frontend/src/features/dashboard/`, composing both modules;
  it is not part of the `life` module and not part of finance.
- `/life/*` pages remain the life module's own detail views; `/finance/*`
  remains finance. Finance's API contract is untouched; the Dashboard is a new
  read-only consumer.
