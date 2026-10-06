"""Egress tests — the offline file-import path makes no network calls at all.

ARCHITECTURE.md §8 scopes this precisely, and the scoping matters:

    The `--network=none` guard proves the local file-import path is
    telemetry-free and offline-capable, not that the app never touches the
    network.

M5 / bead LifeOS-10 makes live HTTPS calls to Enable Banking, and it must:
Enable Banking has no webhooks, so the worker polls it. A blanket "this app
never connects to anything" assertion would therefore be both untrue and
useless — it would fail on the day M5 lands, and the correct way to pass it
would be to delete the test.

So these tests assert a bounded claim: **running the offline pipeline — parse a
local Amex PDF statement, normalise it, assign occurrence indices, and dedup
against the stored fingerprints — issues zero `connect()` syscalls.** A
telemetry SDK would show up here as a DNS lookup and a connect during import.
That is the actual risk being retired: not "the app needs the internet", but
"importing my statement phones home".

Three layers, and the middle one is the one that matters:

1. `test_offline_import_makes_no_connect_calls` — the real claim.
2. `test_the_strace_harness_detects_a_connect_call` — the negative control.
   Without this, layer 1 could pass for the wrong reason: a strace that failed
   to attach, a wrong `-e` expression, an output path that collected nothing.
   An assertion that cannot fail is not an assertion.
3. `test_strace_is_available` — two modes. Locally it skips when `strace` or
   `unshare` is unavailable; under strict mode it fails, because a
   silently-skipped egress test in CI is a green check that checked nothing.
   Strict mode is the mechanism: `LIFEOS_REQUIRE_EGRESS=1` inverts the module's
   default, and the `egress-test` CI job is what sets it.

The namespace is a gate under strict mode, not merely a reported degradation.
`_degrade` — used by `_run_under_trace` and by the availability test — raises
when `LIFEOS_REQUIRE_EGRESS=1`, so a missing `unshare`, a failed `unshare -Urn`
probe, and a missing `strace` all fail the run instead of skipping. The
`egress-test` job relaxes `kernel.apparmor_restrict_unprivileged_userns` on
ubuntu-24.04 to provide the namespace; where it cannot, the strict run fails and
says why.

The subprocess runs inside an unprivileged network namespace with no interfaces
(`unshare -Urn`), which is the host equivalent of `docker run --network=none`:
even if the code did try to reach the network, there is nowhere to go, and the
attempt is recorded rather than succeeding quietly.
"""
