"""finance.background - scheduler + job registry + CLI worker.

Must NOT require a DB connection at import time. All imports are clean.
"""

from __future__ import annotations

# CLI worker entrypoint: python -m finance.background.worker
# This must import cleanly without DB dependencies

# Job registry and scheduler will be implemented in M1
# For M0, this module provides the structure and a health-checkable entrypoint
