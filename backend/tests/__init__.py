"""backend/tests — cross-module tests only (section D).

Module-local tests live in `backend/finance/tests/{unit,integration,fixtures}/`.
This directory holds the tests that need more than one module to be true at once:
the import graph, the app factory, the public surface.
"""
