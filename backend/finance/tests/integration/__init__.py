"""Integration tests: several components together, still no live database.

The pipeline tests run a real adapter through real normalisation into real
dedup. What they do not do is open a connection — anything requiring a live
Postgres belongs with the M1 migrations (bead LifeOS-6), which is when there is
a schema to run against.

No transaction in this directory is real. All of it is synthetic.
"""
