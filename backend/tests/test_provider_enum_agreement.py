"""The provider enum is written down three times, and nothing tied the three together.

Bead `LifeOS-dbn` S8. `V1_PROVIDERS` is the canonical list, but the OpenAPI
prose and the frontend's hand-written `z.enum` are separate spellings of it.
Two of the three can drift silently, and the only symptom is a 400 from the
upload endpoint for a provider the UI happily offered — or a UI offering a
provider the API rejects. No type checker reaches either, because the Zod enum
is not generated from the spec — that generation is unimplemented
(ARCHITECTURE.md §7) — so "the frontend types come from the contract" is not true
(docs/adr/0002-import-provider-enum.md, ADR 0002 Context).

So this module compares the three surfaces to each OTHER, with `V1_PROVIDERS`
as the reference. No test here hardcodes the five value names: a fourth copy is
exactly the drift this file exists to end. The names that ARE spelled out are
the ones that are decisions rather than derivations — the dropped list in ADR
0002, and the uploadable / declared-not-built split the same ADR fixes.
"""

from __future__ import annotations

import re
from pathlib import Path

from finance.api.routes import imports as imports_route
from finance.api.schemas import ImportRequest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The prose surface. Read from the live model rather than from a copy of the
# string, because the description is what reaches the OpenAPI spec.
SCHEMAS_SOURCE = REPO_ROOT / "backend/finance/api/schemas/__init__.py"

# The Zod surface. Hand-maintained in the frontend, NOT generated.
TYPES_TS = REPO_ROOT / "frontend/src/features/finance/imports/types.ts"

# Dropped on the record, with reasons, in ADR 0002: amex_csv RETIRED (the Amex
# app exports PDF only), revolut_csv DEFERRED (no CSV ever supplied),
# google_wallet EXCLUDED from v1 (dedup liability).
DROPPED: tuple[str, ...] = ("amex_csv", "revolut_csv", "google_wallet")

# The split ADR 0002 fixes, as amended 2026-10-03: amex_pdf, rabobank_pdf and
# revolut_pdf all have a built FileAdapter. DECLARED_NOT_BUILT is now EMPTY —
# not because the guard went away, but because there is nothing left to guard.
# It stays, as a name and as a test, so the next provider that is declared and
# not built has somewhere to be recorded and something that goes red if it is
# forgotten.
UPLOADABLE: tuple[str, ...] = ("amex_pdf", "rabobank_pdf", "revolut_pdf")
DECLARED_NOT_BUILT: tuple[str, ...] = ()

#: `import_method` values that mean "this provider is reached by uploading a
#: file". A provider whose method is one of these but which appears in neither
#: half of the split above has been added to the enum and not classified, which
#: is the drift this file exists to end. The other two v1 providers are reached
#: without an upload — `enable_banking` over its API, `manual` as JSON — which
#: is a different state from "listed but not implemented".
UPLOAD_METHODS: tuple[str, ...] = ("csv", "pdf")

_PROSE_PREFIX = "One of:"
_WHITESPACE = re.compile(r"\s+")
# The enum is matched structurally, not as exact text, so quote style, trailing
# commas and line wrapping cannot break the test. Formatting is not the
# contract, and a test that fails on a reformat is a test people delete.
_ZOD_ENUM = re.compile(r"z\.enum\(\s*\[(.*?)\]\s*\)", re.DOTALL)
_STRING_LITERAL = re.compile(r"""(['"])([^'"]+)\1""")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _zod_provider_values() -> tuple[str, ...]:
    """The members of the `z.enum([...])` in types.ts, in source order."""
    match = _ZOD_ENUM.search(_read(TYPES_TS))
    assert match is not None, f"no z.enum([...]) found in {TYPES_TS}"
    body: str = match.group(1)
    values: list[str] = []
    for literal in _STRING_LITERAL.finditer(body):
        value: str = literal.group(2)
        values.append(value)
    return tuple(values)


def _prose_description() -> str:
    """The `ImportRequest.provider` field description, as the API ships it."""
    description = ImportRequest.model_fields["provider"].description
    return description if description is not None else ""


def _prose_provider_values() -> tuple[str, ...]:
    """The provider names listed in that description, split on commas."""
    description = _prose_description()
    assert description.startswith(_PROSE_PREFIX), (
        f"the provider description no longer opens with {_PROSE_PREFIX!r}: "
        f"{description!r}"
    )
    listed = description[len(_PROSE_PREFIX) :]
    return tuple(part.strip() for part in listed.split(","))


def _surfaces() -> dict[str, tuple[str, ...]]:
    """The three live spellings of the provider list, keyed by surface name."""
    return {
        "V1_PROVIDERS": imports_route.V1_PROVIDERS,
        "schemas prose": _prose_provider_values(),
        "types.ts z.enum": _zod_provider_values(),
    }


def test_prose_and_zod_agree_with_the_canonical_provider_list() -> None:
    """Neither hand-maintained spelling may add to or drop from V1_PROVIDERS.

    Order-insensitive: `V1_PROVIDERS` is ordered because ADR 0002 fixes the
    order for the humans reading the decision record, and nothing in the API
    depends on it. Set equality is still exact, though — an extra value drifts
    just as surely as a missing one, and an extra value in the Zod enum is a
    promise the upload endpoint will refuse to keep.
    """
    canonical = set(imports_route.V1_PROVIDERS)
    for name, values in _surfaces().items():
        assert set(values) == canonical, (
            f"{name} does not match V1_PROVIDERS: "
            f"missing={sorted(canonical - set(values))} "
            f"extra={sorted(set(values) - canonical)}"
        )


def test_dropped_providers_stay_dropped_on_every_live_surface() -> None:
    """amex_csv, revolut_csv and google_wallet must not come back (LifeOS-dbn S8).

    This is the regression that actually bit. The retired CSV paths were still
    the live default and the sole fixture for two headline proofs (bead
    LifeOS-4e7), which is what forced ADR 0002 in the first place. Their
    adapter modules have since been deleted, so "the module exists" is evidence
    of nothing here either way — only the three lists count.
    """
    for name, values in _surfaces().items():
        resurrected = sorted(set(DROPPED).intersection(values))
        assert not resurrected, (
            f"{name} resurrected a provider ADR 0002 dropped "
            f"(docs/adr/0002-import-provider-enum.md): {resurrected}"
        )


def test_provider_description_is_still_hand_written_prose() -> None:
    """Stop the prose check above from quietly becoming a tautology.

    Were the description derived from `V1_PROVIDERS`, it would be compared with
    itself and would pass for any list whatsoever. Finding the literal in the
    schema source keeps the third copy hand-maintained, which is what makes it
    correct for the agreement test to fail when the copy drifts. Whitespace is
    collapsed on both sides so that re-wrapping the literal is not a failure.
    """
    description = _WHITESPACE.sub(" ", _prose_description()).strip()
    source = _WHITESPACE.sub(" ", _read(SCHEMAS_SOURCE))
    assert description in source, (
        "the provider description is no longer a literal in "
        f"{SCHEMAS_SOURCE}, so the prose agreement check cannot fail and "
        "therefore proves nothing"
    )


def test_accepts_upload_is_true_exactly_for_providers_with_a_file_adapter() -> None:
    """The flag and the adapter table are two spellings of "is it built?".

    `_provider_info` derives the flag from `_FILE_ADAPTERS`, so the two cannot
    disagree at runtime today. This pins the derivation against a later edit
    that computes the flag some other way, and pins the invariant that makes
    deriving it safe: the table holds no adapter for a provider the enum never
    declared. Such an entry is invisible in the response — `accepts_upload` is
    only ever built from `V1_PROVIDERS` — so the endpoint would reject a
    provider the list advertises as uploadable, and nothing else would notice.
    """
    canonical = set(imports_route.V1_PROVIDERS)
    adapters = set(imports_route._FILE_ADAPTERS)

    assert adapters <= canonical, (
        f"_FILE_ADAPTERS has adapters for undeclared providers: "
        f"{sorted(adapters - canonical)}"
    )

    response = imports_route.list_providers()
    assert {provider.kind for provider in response.providers} == canonical, (
        "the /imports/providers response does not carry V1_PROVIDERS verbatim"
    )
    for provider in response.providers:
        assert provider.accepts_upload == (provider.kind in adapters), (
            f"{provider.kind}: accepts_upload={provider.accepts_upload}, but it "
            f"{'has' if provider.kind in adapters else 'has no'} file adapter"
        )


def test_the_declared_but_not_built_split_is_the_one_adr_0002_fixed() -> None:
    """Pin the current split deliberately, and say why it is spelled out.

    This is the one place the module names providers instead of deriving them,
    because these names are a decision rather than a derivation. ADR 0002 fixes
    amex_pdf, rabobank_pdf and revolut_pdf as the built upload paths; as amended
    on 2026-10-03 there is no provider left in the declared-but-not-built half,
    because both adapters landed and were reconciled against the real statements
    at delta 0. If this test fails, the ADR is what has to change first: a
    reviewer reading a red test here should learn that a decision moved, not that
    a test rotted. The name `DECLARED_NOT_BUILT` and the test around it stay for
    the next provider that is declared and not built, because the gap between
    "listed" and "built" is only visible because the split is pinned.
    """
    canonical = set(imports_route.V1_PROVIDERS)
    assert set(UPLOADABLE).union(DECLARED_NOT_BUILT) <= canonical, (
        "the pinned split names providers outside V1_PROVIDERS"
    )
    assert not set(UPLOADABLE).intersection(DECLARED_NOT_BUILT), (
        "a provider cannot be both built and declared-not-built"
    )

    adapters = set(imports_route._FILE_ADAPTERS)
    assert adapters == set(UPLOADABLE), (
        "the providers with a FileAdapter must be exactly the ones ADR 0002 "
        f"records as uploadable; got {sorted(adapters)}"
    )

    uploadable = {
        provider.kind
        for provider in imports_route.list_providers().providers
        if provider.accepts_upload
    }
    assert uploadable == set(UPLOADABLE), (
        "accepts_upload and the pinned split disagree: "
        f"{sorted(uploadable)} vs {sorted(UPLOADABLE)}"
    )
    for kind in DECLARED_NOT_BUILT:
        assert kind not in uploadable, (
            f"{kind} is declared but unimplemented, so it must report "
            "accepts_upload=false rather than 400 at upload time"
        )


def test_every_upload_shaped_provider_is_classified_by_the_pinned_split() -> None:
    """Stop the empty split from turning into an unexamined one.

    `DECLARED_NOT_BUILT` being empty means the loop above checks nothing, so
    this carries the load in the other direction: a provider added to
    `V1_PROVIDERS` with an upload-shaped `import_method` has to appear in one of
    the two halves of the split. Dropping a new `csv` or `pdf` provider into the
    enum without classifying it is the drift this module exists to end, and with
    nothing declared-not-built that is the only direction left to check.
    """
    classified = set(UPLOADABLE).union(DECLARED_NOT_BUILT)
    upload_shaped = {
        provider.kind
        for provider in imports_route.list_providers().providers
        if provider.import_method in UPLOAD_METHODS
    }

    assert upload_shaped == classified, (
        "every provider reached by uploading a file must be classified as built "
        f"or as declared-not-built; unclassified: {sorted(upload_shaped ^ classified)}"
    )
