"""finance.tests — tests that belong to the finance module.

Section D: `finance/tests/` holds this module's own tests; `backend/tests/` holds
cross-module tests only.

    unit/         pure functions, no I/O. The bulk of the suite.
    integration/  several components together, still no database.
    fixtures/     synthetic data builders.

Nothing here imports a real bank, a real account or a real transaction. Every
fixture is synthetic, and the ones with payee names use invented merchants.
"""
