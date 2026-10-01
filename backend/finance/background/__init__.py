"""finance.background — scheduler, job registry, CLI worker.

Must import cleanly with no database connection and no network. That constraint
is not stylistic: the worker is the process that must still start to report why
it cannot work, and an import-time connection attempt turns a diagnosable
"database is down" into an unstartable process.

Jobs land in M1. What exists now is the registry shape and a health-checkable
entrypoint.
"""

from __future__ import annotations
