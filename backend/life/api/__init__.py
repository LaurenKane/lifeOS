"""life.api — HTTP surface of the life module.

`routes/` holds the FastAPI routers (the only database-touching layer);
`schemas/` is the OpenAPI contract the frontend mirrors; `deps.py` owns the
session lifecycle. The same shape as `finance/api`, per ADR 0010.
"""
