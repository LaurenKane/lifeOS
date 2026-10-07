"""life.domain.services — pure logic, no database.

The same purity standard `finance.domain.services` holds: nothing here opens
a session or imports the ORM; routes own the database. Everything is a pure
function of its arguments so the unit suite runs with no Docker.
"""
