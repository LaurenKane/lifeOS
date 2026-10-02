"""Cross-module import smoke test.

`backend/tests/` holds cross-module tests only (section D): things that need
more than one module to be true at once.

This is the test that would have caught the 27 broken files. Every module in the
import graph is imported here, so a bad relative import fails one test with a
clear name rather than surfacing later as a 500 from a route nobody exercised.

`backend/` is the import root, so `core` and `finance` are top-level packages and
every import below is absolute.
"""

from __future__ import annotations

import importlib

import pytest

# Every module in the graph, in dependency order. A relative import that resolves
# to a non-existent sibling package fails here with the module named.
MODULES = [
    # The import root itself.
    "main",
    "config",
    # core: shared primitives. Depends on nothing in this project.
    "core",
    "core.money",
    "core.datetime",
    "core.blob",
    "core.preference",
    # finance.public: the export surface.
    "finance",
    "finance.public",
    # finance.domain: PRIVATE, pure.
    "finance.domain",
    "finance.domain.models",
    "finance.domain.value_objects",
    "finance.domain.value_objects.money",
    "finance.domain.value_objects.date_range",
    "finance.domain.services",
    "finance.domain.services.dedupe",
    "finance.domain.services.transfer_match",
    "finance.domain.services.categorize",
    "finance.domain.services.budget",
    # finance.ingestion: the pipeline.
    "finance.ingestion",
    "finance.ingestion.fingerprint",
    "finance.ingestion.normalize",
    "finance.ingestion.identity",
    "finance.ingestion.dedupe",
    "finance.ingestion.transfer_match",
    "finance.ingestion.categorize",
    "finance.ingestion.adapters",
    "finance.ingestion.adapters.base",
    "finance.ingestion.adapters.enable_banking",
    "finance.ingestion.adapters.amex_csv",
    "finance.ingestion.adapters.amex_pdf",
    "finance.ingestion.adapters.revolut_csv",
    "finance.ingestion.adapters.manual",
    # finance.api: the outermost layer.
    "finance.api",
    "finance.api.schemas",
    "finance.api.routes",
    "finance.api.routes.accounts",
    "finance.api.routes.transactions",
    "finance.api.routes.imports",
    "finance.api.routes.review",
    "finance.api.routes.categories",
    # finance.background: must import with no DB.
    "finance.background",
    "finance.background.worker",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    """Every module imports.

    The assertion is deliberately just "no exception": importlib raises on
    failure, so a green test means the module loaded. This is the test that
    catches `from ...core.money import Money` in a file that is not three levels
    deep — that resolves to `finance.core.money`, which does not exist.
    """
    assert importlib.import_module(module_name) is not None


def test_backend_is_not_a_package() -> None:
    """`backend/` is the import root, not an importable package.

    A `backend/__init__.py` would make `backend.finance` a second, parallel
    spelling of every module, and the two would hold separate module objects —
    two copies of every dataclass, and `isinstance` failing across them. The
    hierarchy is `core` and `finance`, full stop.
    """
    assert importlib.util.find_spec("backend") is None


def test_core_and_finance_are_siblings_not_nested() -> None:
    """Section D: `core` is a sibling of `finance`, never its parent.

    This is the exact structural claim the import fix depends on. If someone
    nests core under finance to make a relative import work, this fails.
    """
    core = importlib.import_module("core")
    finance = importlib.import_module("finance")
    assert core.__name__ == "core"
    assert finance.__name__ == "finance"
    assert core.__package__ == "core"
    assert finance.__package__ == "finance"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("finance.core")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.core")


def test_services_package_defines_nothing_itself() -> None:
    """`finance/domain/services/__init__.py` is a re-export surface only.

    It once held 369 lines of concatenated duplicated implementations, including
    a stray `Money as Money2` alias. A `__init__` that defines logic is a second
    place for the same rule to exist, and the two copies drift.
    """
    package = importlib.import_module("finance.domain.services")
    expected = {
        "BudgetResult",
        "CategorizeResult",
        "DedupeResult",
        "TransferMatch",
        "categorize_transaction",
        "check_budget",
        "dedupe_check",
        "transfer_match",
    }
    assert set(package.__all__) == expected

    # Every exported name must resolve to the sibling module that owns it.
    for name in package.__all__:
        exported = getattr(package, name)
        owners = [
            module_name
            for module_name in (
                "budget",
                "categorize",
                "dedupe",
                "transfer_match",
            )
            if getattr(
                importlib.import_module(f"finance.domain.services.{module_name}"),
                name,
                None,
            )
            is exported
        ]
        assert owners, (
            f"{name} is defined in services/__init__.py, not a sibling module"
        )

    # And no `Money2`-style aliases.
    assert not [name for name in dir(package) if name.endswith("2")]


def test_worker_imports_without_a_database() -> None:
    """The worker must start when Postgres is down.

    An import-time connection attempt turns a diagnosable "database is
    unavailable" into an unstartable process with no way to ask why.
    """
    worker = importlib.import_module("finance.background.worker")
    report = worker.health_check()
    assert report["status"] == "ok"
    assert all(check == "pass" for check in report["checks"].values())


def test_worker_cli_exits_zero_on_health_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from finance.background.worker import main

    assert main(["health-check"]) == 0
    assert "Health check" in capsys.readouterr().out


def test_worker_cli_exits_nonzero_on_unknown_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown command must fail, not print help and exit 0."""
    from finance.background.worker import main

    assert main(["nonsense"]) == 1
    assert "Unknown command" in capsys.readouterr().out


def test_app_builds_and_serves_the_routes() -> None:
    """`main` mounts the five routers under the API prefix.

    Asserted against the generated OpenAPI paths rather than `app.routes`:
    FastAPI wraps an included router in an `_IncludedRouter` that has no `.path`,
    and the OpenAPI spec is the artefact the frontend's TypeScript is generated
    from anyway. A router that failed to import would produce a spec missing its
    paths while the process still starts green.
    """
    from main import app

    paths = set(app.openapi()["paths"])
    assert "/health" in paths
    assert "/ready" in paths
    for route in ("accounts", "transactions", "imports", "review", "categories"):
        assert f"/api/v1/{route}" in paths, f"{route} router is not mounted"


def test_openapi_spec_generates_typed_response_models() -> None:
    """A route must not declare a response_model with no fields.

    `response_model=list` produces an OpenAPI schema of `{}`, and the TypeScript
    generated from it claims the endpoint returns nothing — a contract that is
    wrong in a way no runtime check catches.
    """
    from main import app

    schema = app.openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert "responses" in operation, f"{method.upper()} {path} has no responses"
            for status, response in operation["responses"].items():
                if status == "204":
                    continue
                assert response.get("content"), (
                    f"{method.upper()} {path} {status} has no body"
                )
