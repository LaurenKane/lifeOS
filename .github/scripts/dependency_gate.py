#!/usr/bin/env python3
# ruff: noqa: T201  -- this is a CLI; its output IS the product
"""Dependency gate — a lockfile may only change if a human approved it.

Why this exists
---------------
The realistic threat here is not a supply-chain attack on a package that was
already reviewed. It is an AI agent quietly adding a dependency.

This project is built by agents, in a repository where agents run linters,
`uv sync`, `npm ci` and arbitrary code from a local filesystem. `uv add
sentry-sdk` in a moment of helpfulness leaves no trace in the review diff that
looks alarming: it looks like one more line in a dependency list, alongside the
fourteen legitimate ones. It also runs install-time code, and it is now trusted
by every gate in CI. Nothing in `ruff`, `mypy` or the invariant checker would
notice.

So the gate is deliberately not about whether the dependency is safe. It is
about whether a *human said yes*.

The rule
--------
`uv.lock` and `frontend/package-lock.json` are the exact dependency graph. A
change to either is permitted only when BOTH of these hold:

1. **The new hash is recorded in `.github/dependency-approvals.txt`.**
   The ledger makes every approved state explicit and reviewable. Without it,
   an approval is a thing someone remembers rather than a thing the repo
   records.

2. **If the ledger itself changed in this commit range, a commit in that range
   carries an `APPROVED:` marker.**

   This is the part that actually stops the agent. Condition 1 alone is
   self-certifying: `echo <newhash> >> ledger` would satisfy it. Requiring an
   `APPROVED:` line in a commit *message* — the one place a tool cannot write by
   accident — is what makes the gate mean something. The marker also has to
   carry a reason, so the approval is legible later.

Two conditions, not one, because each blocks a different mistake: forgetting to
record the approval, and fabricating one.

Usage
-----
    dependency_gate.py                    # check the working tree (CI)
    dependency_gate.py --self-test        # prove the gate blocks what it claims

Environment (set by the workflow):
    DEPENDENCY_GATE_BASE   base commit to diff against. When absent, only
                           condition 1 is evaluated, and the skipped condition
                           is reported rather than quietly passed.
    DEPENDENCY_GATE_HEAD   head commit. Defaults to HEAD.

Exit codes:
    0  every lockfile is approved
    1  at least one lockfile changed without a recorded approval
    2  configuration or tool error
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parents[2]

LEDGER_RELPATH = Path(".github") / "dependency-approvals.txt"

#: The dependency graphs. Deliberately a fixed list, not a glob: a new lockfile
#: should be added here deliberately, in a commit that says why, not discovered
#: by a pattern that silently widens the gate's surface.
LOCKFILES: tuple[Path, ...] = (Path("uv.lock"), Path("frontend/package-lock.json"))

#: `APPROVED: <reason>` — case-sensitive, own line, non-empty reason. An
#: `APPROVED:` echoed inside a log line or a code block does not match.
APPROVED_RE = re.compile(r"^APPROVED:\s*\S.*$", re.MULTILINE)


class GateError(Exception):
    """Raised for conditions that must exit 2, never exit 1."""


@dataclass(frozen=True)
class Finding:
    """One gate violation, with enough context to act on it."""

    lockfile: str
    reason: str
    detail: str


# --------------------------------------------------------------------------
# hashing and ledger parsing
# --------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """SHA-256 of a file's bytes.

    Streamed rather than read whole: these files grow, and the gate should not
    care.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_ledger(text: str) -> dict[str, str]:
    """`sha256  path` lines into `{path: sha256}`.

    Comments and blank lines are skipped so the file can explain itself. A
    duplicate path is an error rather than last-wins: two hashes for one lockfile
    means the ledger contradicts itself, and quietly picking one would pick
    whichever happened to be last.
    """
    entries: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise GateError(
                f"{LEDGER_RELPATH}:{number}: expected '<sha256>  <path>', got {line!r}"
            )
        digest, path = parts
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise GateError(f"{LEDGER_RELPATH}:{number}: not a sha256: {digest!r}")
        if path in entries:
            raise GateError(
                f"{LEDGER_RELPATH}:{number}: {path!r} appears twice with "
                f"different hashes; the ledger contradicts itself"
            )
        entries[path] = digest
    return entries


def read_ledger(root: Path) -> dict[str, str]:
    path = root / LEDGER_RELPATH
    if not path.is_file():
        raise GateError(
            f"{LEDGER_RELPATH} is missing. The gate cannot know what is approved, "
            "and an absent ledger must not read as 'everything is fine'."
        )
    return parse_ledger(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def git(*args: str, cwd: Path) -> str:
    """Run a git command, returning stdout. Raises GateError on failure."""
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GateError(
            f"git {' '.join(args)} failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def commit_messages(root: Path, base: str, head: str) -> str:
    """Every commit message in `base..head`, concatenated."""
    return git("log", "--format=%B%x00", f"{base}..{head}", cwd=root)


def ledger_changed(root: Path, base: str, head: str) -> bool:
    """True when the ledger differs between base and head."""
    relpath = LEDGER_RELPATH.as_posix()
    output = git("diff", "--name-only", base, head, "--", relpath, cwd=root)
    return bool(output.strip())


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def evaluate(
    root: Path,
    *,
    ledger: dict[str, str],
    base: str | None,
    head: str,
) -> tuple[list[Finding], list[str]]:
    """Apply the rule. Returns (findings, notes)."""
    findings: list[Finding] = []
    notes: list[str] = []

    # --- condition 1: every current lockfile hash is in the ledger ----------
    for lockfile in LOCKFILES:
        path = root / lockfile
        if not path.is_file():
            raise GateError(f"lockfile missing: {lockfile}")
        current = sha256_of(path)
        recorded = ledger.get(lockfile.as_posix())
        if recorded is None:
            findings.append(
                Finding(
                    lockfile.as_posix(),
                    "not recorded in the approvals ledger",
                    f"{LEDGER_RELPATH} has no entry for {lockfile}. "
                    "Add a line: `<sha256>  <path>`",
                )
            )
        elif recorded != current:
            findings.append(
                Finding(
                    lockfile.as_posix(),
                    "changed since it was last approved",
                    f"approved: {recorded}\n"
                    f"current:  {current}\n"
                    "If this change is intended: update the ledger, commit with\n"
                    "a message containing `APPROVED: <reason>`, and get a human "
                    "to review the diff of the dependency itself.",
                )
            )

    # --- condition 2: a ledger edit required an APPROVED: marker ------------
    if base is None:
        notes.append(
            "no DEPENDENCY_GATE_BASE supplied: could not verify that any ledger "
            "change in this range carried an `APPROVED:` marker. Condition 1 "
            "was still enforced."
        )
        return findings, notes

    if ledger_changed(root, base, head):
        messages = commit_messages(root, base, head)
        matches = APPROVED_RE.findall(messages)
        if not matches:
            findings.append(
                Finding(
                    LEDGER_RELPATH.as_posix(),
                    "the approvals ledger was edited without an APPROVED: marker",
                    f"Commits in {base[:12]}..{head[:12]} change the ledger but "
                    "none carries a line matching `APPROVED: <reason>`.\n"
                    "This is the case the gate exists for: approving your own "
                    "dependency addition is not an approval. Re-commit with a "
                    "message like `APPROVED: add httpx for M5 (reviewed)`.",
                )
            )
        else:
            notes.append(
                "ledger change carries " + "; ".join(m.strip() for m in matches[:5])
            )

    return findings, notes


# --------------------------------------------------------------------------
# self-test — proof that the gate blocks
# --------------------------------------------------------------------------


_UV_LOCK_V1 = b'# uv.lock\nversion = 1\npackages = ["fastapi"]\n'
_UV_LOCK_V2 = b'# uv.lock\nversion = 1\npackages = ["fastapi", "sentry-sdk"]\n'
_NPM_LOCK_V1 = b'{"lockfileVersion":3,"packages":{}}'
_NPM_LOCK_V2 = b'{"lockfileVersion":3,"packages":{"node_modules/sentry-js":{}}}'


def _write_case_repo(root: Path, uv_bytes: bytes, npm_bytes: bytes) -> None:
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "uv.lock").write_bytes(uv_bytes)
    (root / "frontend" / "package-lock.json").write_bytes(npm_bytes)
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "gate-selftest@example.invalid", cwd=root)
    git("config", "user.name", "gate self-test", cwd=root)
    git("config", "commit.gpgsign", "false", cwd=root)


def _write_ledger(root: Path, uv_bytes: bytes, npm_bytes: bytes) -> None:
    path = root / LEDGER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# approvals ledger (self-test fixture)\n"
        f"{sha256_of_bytes(uv_bytes)}  uv.lock\n"
        f"{sha256_of_bytes(npm_bytes)}  frontend/package-lock.json\n",
        encoding="utf-8",
    )


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _commit_all(root: Path, message: str) -> str:
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root).strip()


def run_self_test() -> int:
    """Four scenarios against a throwaway git repo. Prints PASS/FAIL per case."""
    failures: list[str] = []

    def check(label: str, got: list[Finding], expect_violation: bool) -> None:
        violated = bool(got)
        ok = violated == expect_violation
        status = "PASS" if ok else "FAIL"
        detail = "; ".join(f"{f.lockfile}: {f.reason}" for f in got) or "no findings"
        print(f"[{status}] {label}\n         {detail}")
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory(prefix="dep-gate-selftest-") as tmp:
        root = Path(tmp)

        # ---- case 1: everything already approved -> clean ----------------
        _write_case_repo(root, _UV_LOCK_V1, _NPM_LOCK_V1)
        _write_ledger(root, _UV_LOCK_V1, _NPM_LOCK_V1)
        first = _commit_all(root, "initial import")
        findings, _ = evaluate(root, ledger=read_ledger(root), base=first, head=first)
        check("approved lockfiles pass", findings, expect_violation=False)

        # ---- case 2: lockfile changes, ledger untouched -> BLOCKED --------
        # Exactly the agent scenario: `uv add sentry-sdk`, nothing else.
        (root / "uv.lock").write_bytes(_UV_LOCK_V2)
        second = _commit_all(root, "add sentry-sdk for better error messages")
        findings, _ = evaluate(root, ledger=read_ledger(root), base=first, head=second)
        check(
            "unapproved lockfile change is blocked",
            findings,
            expect_violation=True,
        )

        # ---- case 3: lockfile AND ledger both edited, no APPROVED: -------
        # The self-certifying escape hatch. Must still be blocked.
        _write_ledger(root, _UV_LOCK_V2, _NPM_LOCK_V1)
        third = _commit_all(root, "chore: sync lockfile")
        findings, _ = evaluate(root, ledger=read_ledger(root), base=second, head=third)
        check(
            "silent ledger edit without APPROVED: is blocked",
            findings,
            expect_violation=True,
        )

        # ---- case 4: lockfile AND ledger edited WITH APPROVED: -> pass ---
        # Amending the message rather than adding an empty commit: re-committing
        # with the marker is the actual remedy the gate suggests, and an empty
        # commit would not put the ledger change inside `base..head`.
        git(
            "commit",
            "--amend",
            "-q",
            "-m",
            "APPROVED: add sentry-sdk for M5 error reporting (reviewed)",
            cwd=root,
        )
        fourth = git("rev-parse", "HEAD", cwd=root).strip()
        findings, notes = evaluate(
            root, ledger=read_ledger(root), base=second, head=fourth
        )
        check(
            "reviewed change with APPROVED: marker passes",
            findings,
            expect_violation=False,
        )
        if notes:
            print(f"         note: {notes[0]}")

        # ---- case 5: the marker alone must not be sufficient --------------
        # The marker is required when the ledger changes, but the ledger is what
        # records the approved state. A hash that no longer matches the file on
        # disk must fail even with an APPROVED: marker in range.
        _write_ledger(root, _UV_LOCK_V1, _NPM_LOCK_V1)
        fifth = _commit_all(root, "APPROVED: revert to the original lockfile")
        findings, _ = evaluate(root, ledger=read_ledger(root), base=fourth, head=fifth)
        check(
            "a stale ledger hash fails even with an APPROVED: marker present",
            findings,
            expect_violation=True,
        )

    print()
    if failures:
        print(f"self-test FAILED ({len(failures)} case(s)): {', '.join(failures)}")
        return EXIT_VIOLATION
    print("self-test passed: the gate blocks unapproved and self-approved changes.")
    return EXIT_OK


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    root = Path(args.root).resolve()
    base = os.environ.get("DEPENDENCY_GATE_BASE") or None
    head = os.environ.get("DEPENDENCY_GATE_HEAD") or "HEAD"

    try:
        ledger = read_ledger(root)
        findings, notes = evaluate(root, ledger=ledger, base=base, head=head)
    except GateError as exc:
        print(f"dependency-gate: configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    for note in notes:
        print(f"note: {note}")

    if findings:
        print(
            "dependency-gate: FAILED. A dependency graph changed without a "
            "recorded, human-approved decision.\n"
        )
        for finding in findings:
            print(f"  {finding.lockfile}: {finding.reason}")
            for line in finding.detail.splitlines():
                print(f"      {line}")
            print()
        print(
            "To approve a dependency change deliberately:\n"
            f"  1. add its sha256 to {LEDGER_RELPATH}\n"
            "  2. commit with a message containing `APPROVED: <what and why>`\n"
            "  3. get a human to read the actual dependency's diff\n"
            "\n"
            "Steps 1 and 2 are not a formality. They exist because the most\n"
            "likely way this repo's dependencies change is an agent helping\n"
            "itself to something, unattended."
        )
        return EXIT_VIOLATION

    print("dependency-gate: OK — every lockfile matches a recorded approval.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
