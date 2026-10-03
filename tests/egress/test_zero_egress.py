"""Zero-egress assertion for the offline file-import pipeline.

See tests/egress/__init__.py for why the claim is scoped to the offline path
rather than to the whole application.

Mechanics
---------
The work happens in a child process under two layers of confinement:

    unshare -Urn  →  a fresh user+network namespace with no interfaces.
                     The host equivalent of `docker run --network=none`.
    strace -f -e trace=network -o LOG  →  every network syscall, in every
                     thread and forked child, recorded to LOG.

Both matter. The namespace means a leaked connect() fails instead of quietly
succeeding; strace means the failure is *recorded*, so the test can assert on
it rather than merely observe it.

The driver script is written to a temp file rather than passed with `-c`,
because the driver is long and `strace` output attribution is easier to reason
about when the program has a real file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The driver: run the offline pipeline end to end and report what happened.
#
# It deliberately uses only stdlib plus the project's own ingestion code. No
# TestClient, no fixture server, no monkeypatched httpx: the point is that
# nothing needs patching, because nothing is reaching out in the first place.
_DRIVER = '''\
import json, sys
sys.path.insert(0, {backend!r})

from finance.ingestion.adapters import AmexPdfAdapter
from finance.ingestion.dedupe import (
    ExistingFingerprint,
    assign_occurrence_indices,
    lookup_fingerprint,
)
from finance.ingestion.fingerprint import compute_fingerprint
from finance.ingestion.normalize import normalize_record

# Invented, and shaped like the BIGSERIAL M1 hands out. No real account id.
ACCOUNT_ID = 1001


def fingerprint_scope(account_id):
    """Render an int account id as the string the pinned fingerprint wants.

    ``finance.ingestion.fingerprint`` is SHA-256 hash-pinned, joins its parts
    with ``"|"`` and coerces nothing, so it only accepts a ``str``. An
    unresolved account becomes ``""`` and never ``str(None)``, which would put
    the literal ``"None"`` in the scope key and collide every unattributed row
    into one fingerprint. Duplicated here rather than imported because the
    traced child process deliberately has nothing but ``backend`` on its path.
    """
    return "" if account_id is None else str(account_id)


def extract_lines(payload):
    """The injected text-extraction seam. Local, pure, no I/O.

    The real extractor is M7's pdfplumber dependency and is not in the M0
    dependency set, so the default `extract_pdf_text` is an explicit M0
    placeholder. Injecting a local pure function keeps the egress claim
    meaningful -- it *cannot* reach the network, which is the point -- while
    being honest that this proves the offline path, not PDF text extraction.
    """
    return payload.decode("utf-8").splitlines()


def ingest(payload):
    """Statement bytes -> (normalized records, occurrence indices)."""
    parsed = AmexPdfAdapter(text_extractor=extract_lines).parse(
        payload, account_id=ACCOUNT_ID
    )
    records = [
        normalize_record(
            account_id=raw.account_id,
            description=raw.description,
            amount=raw.amount_minor,
            currency_code=raw.currency,
            booked_date=raw.booked_date,
            line_number=raw.line_number,
        )
        for raw in parsed.records
    ]
    return records, assign_occurrence_indices(records)


def main():
    # A local file, read from disk. Nothing is fetched.
    with open({statement_path!r}, "rb") as fh:
        payload = fh.read()

    records, indices = ingest(payload)

    # First import: every row is new, so store all four fingerprints.
    stored = [
        ExistingFingerprint(
            fingerprint=compute_fingerprint(
                raw_description=record.description,
                raw_amount=record.amount.amount,
                raw_currency=record.amount.currency.code,
                raw_date=record.booked_date.isoformat(),
                account_id=fingerprint_scope(record.account_id),
                occurrence_index=index,
            ),
            account_id=record.account_id,
            source_record_id=position,
        )
        for position, (record, index) in enumerate(zip(records, indices), 1)
    ]

    # Second import of the identical file: must find all four as duplicates.
    _, second_indices = ingest(payload)
    duplicates = sum(
        1
        for record, index in zip(records, second_indices)
        if lookup_fingerprint(
            existing=stored,
            account_id=record.account_id,
            raw_description=record.description,
            raw_amount=record.amount.amount,
            raw_currency=record.amount.currency.code,
            raw_date=record.booked_date.isoformat(),
            occurrence_index=index,
        )
        is not None
    )

    json.dump(
        {{
            "record_count": len(records),
            "stored_count": len(stored),
            "duplicate_count": duplicates,
            "occurrence_indices": list(second_indices),
            "amounts_minor": [r.amount.amount for r in records],
            "descriptions": [r.description for r in records],
        }},
        open({out_path!r}, "w"),
    )


main()
'''

# A driver that deliberately reaches the network. Used only by the negative
# control: if this does NOT produce a connect() in the log, then layer 1's
# "zero connects" result is meaningless.
_CONNECT_DRIVER = """\
import socket
import sys

# AF_INET so glibc has an address family to resolve; 192.0.2.0/24 is
# TEST-NET-1, reserved and unroutable, so nothing leaves the machine even if
# the namespace were not there.
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(2)
try:
    sock.connect(("192.0.2.1", 9))
except OSError as exc:
    print("expected failure:", exc, file=sys.stderr)
finally:
    sock.close()
"""

# Syscall names that mean "the process tried to open a connection".
# `connect` is the load-bearing one; the others are reported alongside it so a
# regression is diagnosable from the failure message instead of needing a
# re-run.
_CONNECT_SYSCALLS = ("connect", "socket", "sendto", "sendmsg", "getaddrinfo")

# strace renders a resumed syscall as `connect(...) = ? <unfinished ...>` and a
# completed one as `connect(3, ...) = 0`. Both are connections.
# `socket(` is deliberately NOT counted as a violation: the stdlib creates
# sockets for things that never touch the network, and on some platforms for
# nothing at all.
_CONNECT_RE = re.compile(r"\bconnect\s*\(", re.MULTILINE)
_UNFINISHED_RE = re.compile(r"\bconnect\s*\(.*<unfinished", re.MULTILINE)


# ===========================================================================
# Strict mode — the difference between a checked gate and a skipped one
# ===========================================================================
#
# Every precondition in this module degrades gracefully. That is right for a
# contributor laptop and wrong for CI, because a skipped egress test reads in
# the log as a passed egress test: the job goes green having checked nothing,
# and the green is the most convincing part.
#
# `LIFEOS_REQUIRE_EGRESS=1` inverts the whole module's default. It is set by
# the `egress-test` job in .github/workflows/ci.yml and by `make
# egress-test-ci`, and nowhere else, so a contributor without strace is not
# blocked from running the suite locally.
#
# An env var rather than a marker or a second test file because the property
# being enforced is the *absence of a skip*, and pytest has no flag for that.
# `--strict-markers`, `-x` and `-W error` all leave a skip a skip.

_TRUTHY = frozenset({"1", "true", "yes", "on"})

EGRESS_REQUIRED = os.environ.get("LIFEOS_REQUIRE_EGRESS", "").strip().lower() in _TRUTHY

_STRACE_MISSING = (
    "strace is not installed. It is the only thing here that *records* a "
    "connect() attempt, so without it the egress claim cannot be checked in "
    "either direction — not proved true and not disproved."
)


def _degrade(detail: str, *, locally: str, can_skip: bool = True) -> None:
    """Report a missing harness precondition. Never hides it.

    Strict mode: always raises. The job fails with the reason attached, which
    is the only outcome that cannot be mistaken for coverage.

    Otherwise: skips when `can_skip` is set, and returns when it is not, so
    the caller can go on with the weaker harness. That second path is the
    behaviour this module has always documented — strace without a namespace
    still records the attempt, it just cannot also make it fail — so it stays
    available locally. It is simply not something CI will accept.
    """
    if EGRESS_REQUIRED:
        raise AssertionError(
            f"{detail}\n"
            "CI sets LIFEOS_REQUIRE_EGRESS=1, which turns this into a failure "
            "instead of a skip: a skipped egress test reads as coverage and is "
            f"not coverage. Locally this would have degraded to: {locally}"
        )
    if can_skip:
        pytest.skip(f"{detail} Locally this degrades to: {locally}")


def _has_strace() -> bool:
    return shutil.which("strace") is not None


def _unshare_argv() -> list[str] | None:
    """The namespace wrapper, or None when it cannot be constructed.

    `unshare -Urn` needs unprivileged user namespaces. Where those are
    disabled (`kernel.unprivileged_userns_clone=0`, some hardened hosts, many
    CI sandboxes), the test still runs and still asserts on strace's record —
    it just loses the belt-and-braces namespace. Reported, never hidden.
    """
    if shutil.which("unshare") is None:
        return None
    probe = subprocess.run(
        ["unshare", "-Urn", "--", "/bin/true"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    return ["unshare", "-Urn", "--"]


def _write_statement_text(path: Path) -> None:
    """A small, entirely synthetic Amex statement text extract. No real data.

    Deliberately *not* a real PDF: see the honesty note in the test docstring.
    What matters here is that these bytes reach the parser, so a regression in
    the fixture format fails this test instead of quietly yielding zero rows.

    Mirrors `finance.tests.fixtures.synthetic_amex_pdf_text()`, which is the
    same statement for the adapter unit and pipeline tests. Kept local because
    the zero-egress proof needs the text *on disk* in a tmp dir, read by a
    separate untraced-interpreter child process that only has `backend` on its
    sys.path. Change both together.

    Layout is the verified Dutch Amex NL `pdftotext -layout` format the parser
    requires: TWO `DD.MM.YY` dates per row (Transactiedatum, then Datum
    verwerkt), and an unsigned European amount at end of line. A credit would
    be a bare `CR` line *beneath* the amount; this statement is all debits and
    the expected amounts_minor below depend on that, so there is no `CR` line.

    Two identical EUR 12.34 rows on one day, so the occurrence indices are
    1 and 2 rather than both 1 — the case where a dedup implementation without
    occurrence indexing silently eats a real purchase.
    """
    path.write_text(
        "Card Summary\n"
        "Transactiedatum Datum verwerkt Omschrijving Bedrag\n"
        "14.03.26  14.03.26  JUMBO 4321 AMSTERDAM REF:000000123456            8,50\n"
        "14.03.26  15.03.26  ALBERT HEIJN 1234 REF:000000123456                12,34\n"
        "14.03.26  14.03.26  ALBERT HEIJN 1234 REF:000000123456                12,34\n"
        "15.03.26  15.03.26  NS INTERCITY REF:000000123456                       4,10\n"
        "New Balance                                                          33,18\n",
        encoding="utf-8",
    )


def _run_under_trace(
    tmp_path: Path, *, driver_source: str, name: str
) -> tuple[int, str, str]:
    """Run a driver under unshare + strace.

    Returns:
        (returncode, strace_log, stderr)

    The strace log is read even when the child fails, because a trace of a
    crash is usually the most useful thing in the failure output.
    """
    driver = tmp_path / f"{name}.py"
    driver.write_text(driver_source, encoding="utf-8")

    log = tmp_path / f"{name}.strace"
    backend = str(REPO_ROOT / "backend")

    argv = [
        "strace",
        "-f",  # follow forks and threads: telemetry likes background threads
        "-qq",
        "-s",  # 256 bytes of string per call, enough to read a hostname
        "256",
        "-e",
        "trace=network",
        "-o",
        str(log),
    ]
    wrapper = _unshare_argv()
    if wrapper is None:
        # The documented degradation: strace still records the attempt, it just
        # cannot also make it fail. Strict mode refuses it, because a claim
        # about an empty network namespace that was checked without the
        # namespace is not the claim.
        _degrade(
            "unprivileged user namespaces are unavailable, so the traced "
            "process would run with a live network stack",
            locally="strace-only observation (a connect() would be recorded, "
            "but nothing would stop it succeeding)",
            can_skip=False,
        )
    else:
        argv.extend(wrapper)
    argv.extend([sys.executable, str(driver)])

    # PYTHONPATH too, not just sys.path: uv-installed console scripts and any
    # child import need it, and it costs nothing.
    env = dict(os.environ)
    env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # No proxy, no ambient endpoint: a stray http client must fail loudly rather
    # than succeed through a proxy that happens to be set in the environment.
    for leak in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ):
        env.pop(leak, None)

    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )
    trace_text = (
        log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    )
    return completed.returncode, trace_text, completed.stderr


def _connect_lines(trace: str) -> list[str]:
    """Every line in the trace that records a connect() attempt."""
    return [
        line.strip()
        for line in trace.splitlines()
        if _CONNECT_RE.search(line) or _UNFINISHED_RE.search(line)
    ]


def _any_network_syscall(trace: str) -> list[str]:
    """Any network-related syscall at all. Diagnostic only, never an assertion."""
    hits: list[str] = []
    for line in trace.splitlines():
        for syscall in _CONNECT_SYSCALLS:
            if re.search(rf"\b{syscall}\s*\(", line):
                hits.append(line.strip())
                break
    return hits


# ===========================================================================
# The claim
# ===========================================================================


class TestOfflineImportHasNoEgress:
    @pytest.fixture(autouse=True)
    def _strace_is_present(self) -> None:
        """The strace precondition, as a fixture rather than a `skipif`.

        A `skipif` is evaluated at import time and cannot be conditional on
        "am I in CI", and a skip is the one outcome that must not reach the CI
        log. As a fixture it runs inside every test in the class, so there is
        exactly one place where a missing strace is decided: a skip for a
        contributor, a failure for CI.
        """
        if _has_strace():
            return
        _degrade(
            _STRACE_MISSING,
            locally="both egress tests below are skipped; the harness "
            "preconditions still run and report",
        )

    def test_offline_import_makes_no_connect_calls(self, tmp_path: Path) -> None:
        """Amex statement -> records -> occurrence indices -> dedup, zero connects.

        Also proves the import actually happened, because "zero connects" is
        equally true of a process that crashed on line 1. The driver writes its
        result to a file and the counts are asserted here, so a broken pipeline
        fails this test rather than passing it quietly.

        Scope of the claim, stated plainly
        ----------------------------------
        This proves the *offline path is telemetry-free*: parsing, normalising,
        occurrence indexing and dedup all run with no `connect()` syscall, under
        strace and in an empty network namespace. That is the property
        ARCHITECTURE.md §8 asserts, and it is asserted against the driver below
        rather than assumed.

        It does NOT prove that a real PDF was parsed. Text extraction is
        injected as a local pure function (`extract_lines` in the driver)
        because the real extractor is M7's `pdfplumber` seam and pdfplumber is
        not in the M0 dependency set. So this test says nothing about whether
        `pdfplumber` itself would egress -- when M7 lands it, the extractor it
        installs becomes part of this path and the harness will cover it from
        that point on. Do not read this as "PDF import is proven"; read it as
        "everything downstream of extraction is offline and non-destructive".
        """
        # Plain text, deliberately: naming it .pdf would imply a real PDF was
        # parsed here. See the honesty note above.
        statement_path = tmp_path / "statement.txt"
        _write_statement_text(statement_path)
        out_path = tmp_path / "result.json"

        source = _DRIVER.format(
            backend=str(REPO_ROOT / "backend"),
            statement_path=str(statement_path),
            out_path=str(out_path),
        )
        returncode, trace, stderr = _run_under_trace(
            tmp_path, driver_source=source, name="offline_import"
        )

        assert returncode == 0, (
            f"the offline pipeline failed under strace (exit {returncode})\n"
            f"--- stderr ---\n{stderr}\n"
            f"--- trace ---\n{trace}"
        )

        connects = _connect_lines(trace)
        assert not connects, (
            "the offline file-import path opened a network connection. "
            "ARCHITECTURE.md §8 requires this path to be telemetry-free.\n"
            "connect() calls observed:\n  " + "\n  ".join(connects)
        )

        # The import worked. Without this, the assertion above would also hold
        # for a driver that did nothing.
        assert out_path.exists(), "the driver produced no result file"
        result = json.loads(out_path.read_text(encoding="utf-8"))

        assert result["record_count"] == 4, result
        assert result["stored_count"] == 4, result
        assert result["duplicate_count"] == 4, (
            "re-importing the same file must find all four rows as duplicates; "
            f"got {result['duplicate_count']}\n{result}"
        )
        # Rows 2 and 3 are the identical Albert Heijn pair, so they get 1 and 2.
        # Without the occurrence index both would be 1, the second would be
        # swallowed as a duplicate of the first, and the user's spending would
        # be understated by 12.34.
        assert result["occurrence_indices"] == [1, 1, 2, 1], result
        # Signed BIGINT minor units, never floats. -8.50 EUR -> -850.
        assert result["amounts_minor"] == [-850, -1234, -1234, -410], result
        assert all(isinstance(amount, int) for amount in result["amounts_minor"])

    def test_the_strace_harness_detects_a_connect_call(self, tmp_path: Path) -> None:
        """The negative control. Without it, the test above proves nothing.

        Same wrapper, same trace expression, same namespace — but the driver
        really does call connect(). If that connect() does not appear in the
        log, then the "zero connects" result came from a broken harness and the
        test is vacuous.

        The address is TEST-NET-1 (192.0.2.0/24, reserved and unroutable) and the
        namespace has no route anyway, so the call is guaranteed to fail. What is
        asserted is the *attempt*, which is what leaks.
        """
        returncode, trace, stderr = _run_under_trace(
            tmp_path, driver_source=_CONNECT_DRIVER, name="negative_control"
        )

        # connect() to an unroutable address fails. That is the point: the
        # attempt is what a telemetry leak looks like, successful or not.
        assert returncode != 0 or "expected failure" in stderr, (
            f"the negative-control driver unexpectedly succeeded (exit {returncode})"
        )

        connects = _connect_lines(trace)
        assert connects, (
            "the strace harness did NOT record a connect() call, so the "
            "'zero connect() calls' assertion in "
            "test_offline_import_makes_no_connect_calls is vacuous — it would "
            "pass no matter what the code did.\n"
            f"--- full trace ---\n{trace}"
        )


# ===========================================================================
# Harness preconditions
# ===========================================================================


class TestHarnessPreconditions:
    def test_strace_is_available(self) -> None:
        """The strace precondition, and the reason CI cannot silently skip.

        This is the test that makes a green egress gate mean something. Every
        other test in this module is guarded by the strace precondition, so a
        runner without strace reports "skipped" and the gate goes green having
        checked nothing.

        Under LIFEOS_REQUIRE_EGRESS=1 that is a hard failure (see `_degrade`),
        which is what the CI job relies on. Locally it is a skip, because a
        contributor who cannot install strace is not in a position to fix it
        and should still be able to run the suite. One rule, two modes:

            no strace  ->  skip locally,  fail in CI

        An unconditional `assert` here used to satisfy the CI half and break the
        local half: `make egress-test` failed on any laptop without strace,
        which is both unfriendly and inconsistent with every other test here.
        """
        if _has_strace():
            return
        _degrade(
            _STRACE_MISSING,
            locally="nothing in this module can be checked, so all of it skips "
            "(`apt-get install -y strace` to run it)",
        )

    def test_the_trace_file_is_actually_written(self, tmp_path: Path) -> None:
        """strace -o must produce a file, even for a trivial child.

        A trace log that silently never gets created would make every
        connect()-counting assertion trivially pass.
        """
        # Without this guard the `strace` below raises FileNotFoundError, which
        # is a red run on a machine that simply has no strace. Same rule as
        # everywhere else in this module.
        if not _has_strace():
            _degrade(
                _STRACE_MISSING,
                locally="the trace file cannot be checked without strace",
            )
        driver = tmp_path / "noop.py"
        driver.write_text("value = 1 + 1\n", encoding="utf-8")
        log = tmp_path / "noop.strace"

        wrapper = _unshare_argv() or []
        completed = subprocess.run(
            [
                "strace",
                "-f",
                "-qq",
                "-s",
                "256",
                "-e",
                "trace=network",
                "-o",
                str(log),
                *wrapper,
                sys.executable,
                str(driver),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        assert log.exists(), f"strace wrote no output file at {log}"

    def test_unshare_namespace_is_available_or_reported(self) -> None:
        """The namespace precondition: reported locally, enforced in CI.

        Where `unshare -Urn` is unavailable the tests still run and still assert
        on strace's record; they just lose the network-namespace belt. This
        test surfaces that difference instead of letting it pass unnoticed,
        because the namespaces are load-bearing on the machine the claim was
        written about — and under LIFEOS_REQUIRE_EGRESS=1 it stops being a
        difference CI accepts at all.
        """
        wrapper = _unshare_argv()
        if wrapper is None:
            # Two different reasons for `None`, and the message has to
            # distinguish them. Only shell out when `unshare` is actually
            # installed: on a host without it, the probe would raise
            # FileNotFoundError and turn a reportable skip into a red run.
            if shutil.which("unshare") is None:
                detail = "unshare is not installed"
            else:
                detail = (
                    subprocess.run(
                        ["unshare", "-Urn", "--", "/bin/true"],
                        capture_output=True,
                        text=True,
                        check=False,
                    ).stderr.strip()
                    or "unshare -Urn failed with no diagnostic"
                )
            _degrade(
                f"unprivileged network namespaces are unavailable here ({detail})",
                locally="the egress tests fall back to strace-only observation",
            )
        # If we get here the namespace exists; prove it is actually empty.
        probe = subprocess.run(
            [
                *wrapper,
                sys.executable,
                "-c",
                "import socket; print(len(socket.if_nameindex()))",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert probe.returncode == 0, probe.stderr
        interfaces = probe.stdout.strip()
        assert interfaces == "1", (
            "the namespace should contain only the loopback interface, got "
            f"{interfaces} interfaces — something has a route out"
        )
