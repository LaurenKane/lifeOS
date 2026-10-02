"""Negative tests for the four Life OS invariants.

The bead's acceptance criterion is not "the checker passes". It is:

    "A deliberate attempt to violate each invariant FAILS CI -- a negative test
     per invariant, not just a positive check."

So for every invariant these tests build a throwaway fixture containing a
deliberate violation, run the checker against it in a subprocess, and assert it
is caught. They also assert the *inverse*: that legal constructs are NOT
flagged, because a checker that flags everything is as useless as one that flags
nothing.

Every fixture lives in tmp_path. No real repo file is ever mutated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_invariants.py"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

pytestmark = pytest.mark.skipif(
    not CHECKER.is_file(), reason="scripts/check_invariants.py missing"
)


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


class Fixture:
    """A throwaway repo root plus its own invariants.yaml."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.config_path = tmp_path / "invariants.yaml"

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_config(self, invariants: list[dict]) -> None:
        self.config_path.write_text(
            yaml.safe_dump({"version": 1, "invariants": invariants}, sort_keys=False),
            encoding="utf-8",
        )

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--config", str(self.config_path),
             "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
        )

    def run_json(self, *args: str) -> tuple[int, dict]:
        proc = self.run("--json", *args)
        try:
            return proc.returncode, json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise AssertionError(
                f"checker did not emit JSON on stdout (rc={proc.returncode})\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            ) from exc


def raw_data_config(**overrides) -> list[dict]:
    inv = {
        "name": "raw_data_immutable",
        "kind": "forbid_regex",
        "severity": "error",
        "scan_paths": ["src"],
        "patterns": [
            r'(?i)\bUPDATE\s+source_record\s+SET\b[^;]*\braw_(data|description)\b',
            r'(?i)\bDELETE\s+FROM\s+source_record\b',
        ],
    }
    inv.update(overrides)
    return [inv]


def cross_schema_config(**overrides) -> list[dict]:
    inv = {
        "name": "no_cross_schema_fk",
        "kind": "forbid_regex",
        "severity": "error",
        "scan_paths": ["migrations"],
        "patterns": [r'(?i)\bREFERENCES\s+(?!\s*"?public"?\s*\.\s*)"?([a-z_][a-z0-9_]*)"?\s*\.'],
    }
    inv.update(overrides)
    return [inv]


# ==========================================================================
# 1. raw_data_immutable
# ==========================================================================


def test_update_of_raw_data_is_caught(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(raw_data_config())
    fx.write("src/db.py", "conn.execute('UPDATE source_record SET raw_data = :d')\n")

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert not payload["ok"]
    assert payload["violations"], "expected at least one violation"
    v = payload["violations"][0]
    assert v["invariant"] == "raw_data_immutable"
    assert v["path"] == "src/db.py"
    assert v["line"] == 1, "violation must point at the offending line"


def test_update_of_raw_description_is_caught(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(raw_data_config())
    fx.write("src/db.py", "x = 1\ny = 2\nUPDATE source_record SET raw_description = 'clean'\n")

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    v = payload["violations"][0]
    assert v["line"] == 3, "line number must survive leading context lines"


def test_delete_from_source_record_is_caught(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(raw_data_config())
    fx.write("src/purge.py", "DELETE FROM source_record WHERE id = 42\n")

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert payload["violations"][0]["invariant"] == "raw_data_immutable"


@pytest.mark.parametrize(
    "legal_sql",
    [
        "UPDATE source_record SET status = 'booked'",
        "UPDATE source_record SET merchant = 'Albert Heijn'",
        "UPDATE import_batch SET completed_at = now()",
        "SELECT raw_description FROM source_record",
        "INSERT INTO source_record (raw_data) VALUES ('{}')",
        "DELETE FROM import_batch WHERE id = 1",
    ],
)
def test_legal_sql_is_not_flagged(tmp_path: Path, legal_sql: str):
    """The negative half of the invariant.

    A checker that flags every UPDATE on source_record is as useless as one that
    flags none -- it would train everyone to ignore it. This is the specific
    regression guard for the inverted-logic bug: an earlier draft used a negative
    lookahead and flagged the legal statement while ignoring the illegal one.
    """
    fx = Fixture(tmp_path)
    fx.write_config(raw_data_config())
    fx.write("src/db.py", f"conn.execute({legal_sql!r})\n")

    code, payload = fx.run_json()
    assert code == EXIT_OK, f"legal SQL wrongly flagged: {legal_sql}\n{payload}"
    assert payload["violations"] == []


# ==========================================================================
# 2. no_cross_schema_fk
# ==========================================================================


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (id BIGINT REFERENCES health.account(id));",
        "REFERENCES finance.account(id)",
        "REFERENCES \"health\".account(id)",
    ],
)
def test_cross_schema_fk_is_caught(tmp_path: Path, sql: str):
    fx = Fixture(tmp_path)
    fx.write_config(cross_schema_config())
    fx.write("migrations/001.py", f'op.execute("""{sql}""")\n')

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert payload["violations"][0]["invariant"] == "no_cross_schema_fk"


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (id BIGINT REFERENCES currency(code));",
        "CREATE TABLE t (id BIGINT REFERENCES public.currency(code));",
        "CREATE TABLE t (id BIGINT REFERENCES institution(id) ON DELETE CASCADE);",
        "CREATE TABLE t (currency CHAR(3) NOT NULL REFERENCES currency(code));",
    ],
)
def test_same_schema_fk_is_not_flagged(tmp_path: Path, sql: str):
    """Unqualified REFERENCES means public/same-schema and must be allowed.

    Section E's schema leans on unqualified REFERENCES heavily; flagging those
    would make the invariant useless against the very schema it protects.
    """
    fx = Fixture(tmp_path)
    fx.write_config(cross_schema_config())
    fx.write("migrations/001.py", f'op.execute("""{sql}""")\n')

    code, payload = fx.run_json()
    assert code == EXIT_OK, f"same-schema FK wrongly flagged: {sql}\n{payload}"


# ==========================================================================
# 3. migrations_immutable
# ==========================================================================


def _manifest_config() -> list[dict]:
    return [{
        "name": "migrations_immutable",
        "kind": "manifest",
        "severity": "error",
        "manifest": "scripts/migrations.lock.json",
        "scan_paths": ["backend"],
    }]


def _write_manifest(fx: Fixture, revisions: dict[str, str], comment: str = "test manifest") -> None:
    fx.write(
        "scripts/migrations.lock.json",
        json.dumps({"_comment": comment, "revisions": revisions}, indent=2),
    )


def test_editing_an_applied_migration_is_caught(tmp_path: Path):
    import hashlib

    fx = Fixture(tmp_path)
    fx.write_config(_manifest_config())
    original = 'revision = "0001"\n'
    fx.write("backend/finance/alembic/versions/0001.py", original)
    digest = hashlib.sha256(original.encode()).hexdigest()
    _write_manifest(fx, {"backend/finance/alembic/versions/0001.py": digest})

    code, payload = fx.run_json()
    assert code == EXIT_OK, payload

    fx.write("backend/finance/alembic/versions/0001.py", original + "\n# sneaky edit\n")
    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert payload["violations"][0]["invariant"] == "migrations_immutable"
    assert "edited" in payload["violations"][0]["message"]


def test_new_unapplied_migration_is_allowed(tmp_path: Path):
    """A migration absent from the manifest is UNAPPLIED, which is legal.

    This is the inverse guarantee: the invariant protects applied history, it
    does not forbid writing new revisions. Getting this backwards would block
    every future migration.
    """
    fx = Fixture(tmp_path)
    fx.write_config(_manifest_config())
    _write_manifest(fx, {})
    fx.write("backend/finance/alembic/versions/0001.py", 'revision = "0001"\n')
    fx.write("backend/finance/alembic/versions/0002.py", 'revision = "0002"\n')

    code, payload = fx.run_json()
    assert code == EXIT_OK, payload
    assert payload["violations"] == []


def test_deleted_applied_migration_is_caught(tmp_path: Path):
    import hashlib

    fx = Fixture(tmp_path)
    fx.write_config(_manifest_config())
    original = 'revision = "0001"\n'
    digest = hashlib.sha256(original.encode()).hexdigest()
    _write_manifest(fx, {"backend/finance/alembic/versions/0001.py": digest})

    code, payload = fx.run_json()  # file never written -> recorded but missing
    assert code == EXIT_VIOLATION, payload
    assert "missing" in payload["violations"][0]["message"]


def test_malformed_manifest_is_a_config_error(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(_manifest_config())
    fx.write("scripts/migrations.lock.json", "{not json")

    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload
    assert payload["violations"] == []


def test_manifest_without_revisions_key_is_a_config_error(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(_manifest_config())
    fx.write("scripts/migrations.lock.json", json.dumps({"revisions": []}))

    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload


# ==========================================================================
# 4. fingerprint_frozen
# ==========================================================================


def _hash_config(sha: str) -> list[dict]:
    return [{
        "name": "fingerprint_frozen",
        "kind": "hash",
        "severity": "error",
        "path": "backend/finance/ingestion/fingerprint.py",
        "sha256": sha,
    }]


def test_editing_the_pinned_fingerprint_is_caught(tmp_path: Path):
    import hashlib

    fx = Fixture(tmp_path)
    fx.write_config(_hash_config(""))
    body = "def fingerprint(x: str) -> str:\n    return x\n"
    fx.write("backend/finance/ingestion/fingerprint.py", body)
    digest = hashlib.sha256(body.encode()).hexdigest()

    fx.write_config(_hash_config(digest))
    code, payload = fx.run_json()
    assert code == EXIT_OK, payload

    fx.write("backend/finance/ingestion/fingerprint.py", body + "\n# tweak\n")
    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert payload["violations"][0]["invariant"] == "fingerprint_frozen"


def test_unpinned_fingerprint_is_a_violation(tmp_path: Path):
    """sha256: '' means any future edit would go undetected.

    Treating this as a skip would silently disable the invariant, which is the
    exact failure mode this milestone exists to prevent.
    """
    fx = Fixture(tmp_path)
    fx.write_config(_hash_config(""))
    fx.write("backend/finance/ingestion/fingerprint.py", "x = 1\n")

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert "UNPINNED" in payload["violations"][0]["message"]


def test_missing_fingerprint_file_is_a_violation(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config(_hash_config("a" * 64))

    code, payload = fx.run_json()
    assert code == EXIT_VIOLATION, payload
    assert "missing" in payload["violations"][0]["message"]


# ==========================================================================
# cross-cutting: a broken checker must never read as a policy violation
# ==========================================================================


def test_missing_config_exits_two(tmp_path: Path):
    fx = Fixture(tmp_path)
    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload
    assert payload["violations"] == []


def test_unparseable_config_exits_two(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.config_path.write_text("version: 1\n  bad: [unclosed\n", encoding="utf-8")

    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload
    assert payload["violations"] == []


def test_config_without_invariants_exits_two(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.config_path.write_text(yaml.safe_dump({"version": 1}), encoding="utf-8")

    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload


def test_unknown_invariant_name_exits_two(tmp_path: Path):
    """`--invariant typo` must not be mistaken for 'that invariant passed'."""
    fx = Fixture(tmp_path)
    fx.write_config(_hash_config("a" * 64))
    fx.write("backend/finance/ingestion/fingerprint.py", "x = 1\n")

    code, payload = fx.run_json("--invariant", "no_such_invariant")
    assert code == EXIT_ERROR, payload


def test_unknown_kind_exits_two_not_one(tmp_path: Path):
    fx = Fixture(tmp_path)
    fx.write_config([{"name": "weird", "kind": "telepathy", "scan_paths": ["src"]}])
    fx.write("src/a.py", "x = 1\n")

    code, payload = fx.run_json()
    assert code == EXIT_ERROR, payload


def test_all_invariants_clean_exits_zero(tmp_path: Path):
    """Positive control: a fully clean tree must pass, or the negatives are moot."""
    fx = Fixture(tmp_path)
    fx.write_config(
        raw_data_config(scan_paths=["src"])
        + cross_schema_config(scan_paths=["migrations"])
        + _manifest_config()
    )
    _write_manifest(fx, {})
    fx.write("src/db.py", "UPDATE source_record SET status = 'booked'\n")
    fx.write("migrations/001.py", "REFERENCES currency(code)\n")

    code, payload = fx.run_json()
    assert code == EXIT_OK, payload
    assert payload["ok"] is True
    assert payload["checked"] == 3
    assert payload["violations"] == []
