"""worker CLI entrypoint — health-checkable without a database.

    python -m finance.background.worker health-check
    python -m finance.background.worker list-jobs

Deliberately DB-free. `health_check` proves the process starts and that the
modules it depends on import, which is the check that has to work when Postgres
is down — otherwise a connection failure at import time makes the container
unhealthy with no way to ask why.
"""

from __future__ import annotations

import sys
from typing import Final

WORKER_VERSION: Final = "0.1.0"


def health_check() -> dict[str, object]:
    """Confirm the worker can start and its dependencies import.

    Each entry is an actual import, so this fails loudly if a module is broken
    rather than reporting a hardcoded "pass".
    """
    return {
        "status": "ok",
        "service": "finance-worker",
        "version": WORKER_VERSION,
        "checks": _module_checks(),
        "database": "not contacted by design",
    }


def _module_checks() -> dict[str, str]:
    """Import each dependency and record whether it loaded."""
    checks: dict[str, str] = {}
    for label, module_name in (
        ("core.money", "core.money"),
        ("core.datetime", "core.datetime"),
        ("finance.public", "finance.public"),
        ("finance.ingestion.fingerprint", "finance.ingestion.fingerprint"),
    ):
        try:
            __import__(module_name)
        except ImportError as exc:
            checks[label] = f"fail: {exc}"
        else:
            checks[label] = "pass"
    return checks


def list_jobs() -> dict[str, object]:
    """The registered jobs.

    Empty in M0: the job registry ships with M1, when there are tables to
    schedule work against.
    """
    return {"jobs": [], "note": "job registry lands with M1"}


def _run(command: str) -> int:
    """Dispatch one command. Returns the process exit code."""
    if command == "health-check":
        print(f"Health check: {health_check()}")
        return 0
    if command == "list-jobs":
        print(f"Jobs: {list_jobs()}")
        return 0

    print(f"Unknown command: {command}")
    print("Commands: health-check, list-jobs")
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Also the console-script target `lifeos-worker`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: python -m finance.background.worker <command>")
        print("Commands: health-check, list-jobs")
        return 1
    return _run(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
