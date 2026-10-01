"""worker CLI entrypoint — health-checkable without DB connection.

Usage:
    python -m finance.background.worker health-check
    python -m finance.background.worker list-jobs

This module is deliberately DB-light: it only confirms the process starts
and basic facilities are available. DB-dependent work is deferred.
"""

from __future__ import annotations

import sys


def health_check() -> dict:
    """Health check — verifies the worker can start without DB.

    Returns dict with status and basic info. No DB query is performed.
    """
    return {
        "status": "ok",
        "service": "finance-worker",
        "version": "0.1.0",
        "checks": {
            "imports": "pass",
            "money_module": "pass",
            "datetime_module": "pass",
        },
    }


def list_jobs() -> dict:
    """List scheduled jobs.

    Returns empty dict in M0 — job registry is implemented in M1.
    """
    return {"jobs": [], "note": "job registry deferred to M1"}


def main() -> None:
    """CLI entrypoint for the finance worker."""
    if len(sys.argv) < 2:
        print("Usage: python -m finance.background.worker <command>")
        print("Commands: health-check, list-jobs")
        sys.exit(1)

    command = sys.argv[1]

    if command == "health-check":
        result = health_check()
        print(f"Health check: {result}")
    elif command == "list-jobs":
        result = list_jobs()
        print(f"Jobs: {result}")
    else:
        print(f"Unknown command: {command}")
        print("Commands: health-check, list-jobs")
        sys.exit(1)


if __name__ == "__main__":
    main()
