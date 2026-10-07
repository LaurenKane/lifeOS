"""life.domain — PRIVATE module internals.

The ORM (`models`), the services (capture parsing, cadence math, the Do-now
board) and nothing exported further. Nothing outside the `life` module imports
this; `life.public` is the only surface other modules may touch, mirroring the
rule `backend/finance/public.py` states in its own docstring.
"""
