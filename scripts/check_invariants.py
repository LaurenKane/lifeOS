#!/usr/bin/env python3
"""Machine-checkable invariant enforcement for Life OS.

Four invariants, declared in invariants.yaml:

  raw_data_immutable   source_record.raw_data / .raw_description are never
                       updated or deleted.
  no_cross_schema_fk   no foreign keys across Postgres schemas.
  migrations_immutable applied migrations are never edited.
  fingerprint_frozen   backend/finance/ingestion/fingerprint.py is hash-pinned.

M0 ships the enforcement machinery. The Alembic migrations these checks police
-- including the deferrable balance trigger and the raw_data_immutable DB
trigger -- are written in M1 (bead LifeOS-6). An empty manifest is therefore
the honest M0 state; fabricating migration entries would defeat the purpose.

Design constraints:
  * stdlib only, plus PyYAML. Imports nothing from backend/ or the project
    package, so it runs in CI before any dependency install.
  * Exit codes are strictly three-valued so a broken checker can never be
    mistaken for a policy violation:
        0  all invariants pass
        1  at least one invariant violated   <- CI keys on this
        2  config or tool error
  * Regex scanning of SQL embedded in Python migration files is inherently
    approximate. It is a guardrail against obvious breakage, not a parser-grade
    guarantee. A false negative here means a human or M1's DB triggers catch it.

Usage:
    python3 scripts/check_invariants.py
    python3 scripts/check_invariants.py --json
    python3 scripts/check_invariants.py --invariant no_cross_schema_fk
    python3 scripts/check_invariants.py --config PATH --root PATH
    python3 scripts/check_invariants.py --update [--yes]
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_CONFIG_NAME = "invariants.yaml"
MANIFEST_REVISIONS_KEY = "revisions"


class ConfigError(Exception):
    """Raised for any condition that must exit 2 rather than 1."""


# --------------------------------------------------------------------------
# loading helpers
# --------------------------------------------------------------------------


def load_yaml(path: Path) -> dict[str, Any]:
    """Load the invariant config. Raises ConfigError on any problem."""
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConfigError(
            "PyYAML is required but not importable. Install project deps first."
        ) from exc
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise ConfigError(f"config is not parseable YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config must be a mapping, got {type(data).__name__}: {path}")
    invariants = data.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        raise ConfigError(f"config has no non-empty 'invariants' list: {path}")
    for idx, inv in enumerate(invariants):
        if not isinstance(inv, dict):
            raise ConfigError(
                f"config entry {idx} is {type(inv).__name__}, expected a mapping: {path}"
            )
        for required in ("name", "kind"):
            if required not in inv:
                raise ConfigError(
                    f"config entry {idx} is missing required key '{required}': {path}"
                )
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# file walking
# --------------------------------------------------------------------------


def iter_source_files(
    root: Path, scan_paths: list[str], exclude_globs: list[str] | None
) -> Iterator[Path]:
    """Yield files under scan_paths, relative to root, honouring exclude_globs."""
    seen: set[Path] = set()
    for entry in scan_paths:
        base = (root / entry).resolve()
        if not base.exists():
            continue
        candidates: Iterator[Path] = (
            [base] if base.is_file() else base.rglob("*")
        )
        for path in candidates:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else resolved.as_posix()
            if exclude_globs and any(
                fnmatch.fnmatch(rel, pattern) for pattern in exclude_globs
            ):
                continue
            yield path


# --------------------------------------------------------------------------
# checkers
# --------------------------------------------------------------------------


def violation(
    invariant: str, path: str, line: int | None, match: str, message: str
) -> dict[str, Any]:
    return {
        "invariant": invariant,
        "path": path,
        "line": line,
        "match": match,
        "message": message,
    }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def check_forbid_regex(inv: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    name = inv["name"]
    patterns = inv.get("patterns") or []
    scan_paths = inv.get("scan_paths") or []
    exclude_globs = inv.get("exclude_globs") or []
    if not patterns:
        rel = scan_paths[0] if scan_paths else ""
        return [violation(name, rel, None, "",
                          "invariant declares kind 'forbid_regex' but has no patterns")]
    found: list[dict[str, Any]] = []

    for path in iter_source_files(root, scan_paths, exclude_globs):
        text = _read_text(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        for raw in patterns:
            try:
                compiled = re.compile(raw)
            except re.error as exc:
                raise ConfigError(f"{name}: bad regex {raw!r}: {exc}") from exc
            for match in compiled.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                snippet = match.group(0).strip()
                found.append(
                    violation(name, rel, line, snippet[:200],
                              f"forbidden construct matched {raw!r}")
                )
    return found


def _load_manifest(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ConfigError(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"manifest must be a JSON object: {path}")
    revisions = data.get(MANIFEST_REVISIONS_KEY)
    if revisions is None:
        raise ConfigError(
            f"manifest has no {MANIFEST_REVISIONS_KEY!r} key: {path}. "
            f'Expected {{"_comment": ..., "{MANIFEST_REVISIONS_KEY}": {{}}}}.'
        )
    if not isinstance(revisions, dict):
        raise ConfigError(f"manifest {MANIFEST_REVISIONS_KEY!r} must be an object: {path}")
    return revisions


def check_manifest(inv: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Applied migrations are never edited.

    The manifest lists only APPLIED revisions. A migration file absent from the
    manifest is by definition unapplied, and is therefore allowed.
    """
    name = inv["name"]
    manifest_rel = inv.get("manifest")
    if not manifest_rel:
        return [violation(name, "", None, "", "manifest invariant has no 'manifest' path")]

    manifest_path = root / manifest_rel
    try:
        revisions = _load_manifest(manifest_path)
    except FileNotFoundError:
        return [violation(name, manifest_rel, None, "",
                          "migration manifest not found; cannot prove migrations are immutable")]

    found: list[dict[str, Any]] = []
    for rel, expected in sorted(revisions.items()):
        target = root / rel
        if not target.is_file():
            found.append(violation(name, rel, None, "",
                                   "recorded as an APPLIED migration but the file is missing"))
            continue
        actual = sha256_file(target)
        if actual != expected:
            found.append(violation(
                name, rel, None, f"{expected[:16]}... -> {actual[:16]}...",
                "APPLIED migration was edited (hash drift); add a new revision instead"))
    return found


def check_hash(inv: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    name = inv["name"]
    rel = inv.get("path")
    pinned = (inv.get("sha256") or "").strip()
    if not rel:
        return [violation(name, "", None, "", "hash invariant has no 'path'")]

    target = root / rel
    if not target.is_file():
        return [violation(name, rel, None, "", f"pinned file is missing; cannot prove {name}")]

    actual = sha256_file(target)
    if not pinned:
        # An unpinned file is a real hole: any future edit would go undetected.
        return [violation(name, rel, None, actual,
                          f"{name} is UNPINNED - run `check_invariants.py --update` and commit the result")]

    if actual != pinned:
        return [violation(
            name, rel, None, f"{pinned[:16]}... -> {actual[:16]}...",
            "hash drift: this file is frozen because historical raw_data must stay "
            "parseable and replay must reproduce entries exactly")]
    return []


CHECKERS = {
    "forbid_regex": check_forbid_regex,
    "manifest": check_manifest,
    "hash": check_hash,
}


def run_invariant(inv: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    kind = inv["kind"]
    checker = CHECKERS.get(kind)
    if checker is None:
        # A misconfigured checker is a config error, not a policy violation.
        # Reporting it as exit 1 would mean a broken invariants.yaml looks
        # exactly like a real breach.
        raise ConfigError(
            f"invariant {inv['name']!r} has unknown kind {kind!r}; "
            f"expected one of {sorted(CHECKERS)}"
        )
    return checker(inv, root)


# --------------------------------------------------------------------------
# --update (comment-preserving)
# --------------------------------------------------------------------------


def _replace_sha256_line(text: str, inv_name: str, new_sha: str) -> tuple[str, bool]:
    """Replace the sha256: line belonging to one invariant, preserving comments.

    Deliberately line-based rather than yaml.dump(), which would round-trip the
    whole document and strip every comment -- and invariants.yaml's comments
    carry the rules and the M0/M1 boundary.
    """
    lines = text.splitlines(keepends=True)
    in_target = False
    header = re.compile(r"^-\s+name:\s*[\"']?%s[\"']?\s*$" % re.escape(inv_name))
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^-\s+name:", stripped):
            in_target = header.match(stripped) is not None
            continue
        if in_target and stripped.startswith("sha256:"):
            indent = line[: len(line) - len(line.lstrip())]
            newline = "\n" if line.endswith("\n") else ""
            lines[idx] = f'{indent}sha256: "{new_sha}"{newline}'
            return "".join(lines), True
    return text, False


def _rewrite_manifest_text(original: str, revisions: dict[str, str]) -> str:
    """Rewrite the lock file preserving its _comment keys."""
    data: dict[str, Any] = {}
    if original.strip():
        try:
            loaded = json.loads(original)
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}
    data[MANIFEST_REVISIONS_KEY] = dict(sorted(revisions.items()))
    return json.dumps(data, indent=2) + "\n"


def do_update(config: Path, root: Path, assume_yes: bool) -> int:
    config_data = load_yaml(config)
    invariants = config_data["invariants"]
    original = config.read_text(encoding="utf-8")
    text = original
    changes: list[str] = []
    manifest_updates: dict[str, dict[str, str]] = {}

    for inv in invariants:
        name = inv["name"]
        if inv["kind"] == "hash":
            rel = inv.get("path")
            if not rel:
                continue
            target = root / rel
            if not target.is_file():
                print(f"  {name}: target {rel} does not exist - skipping")
                continue
            actual = sha256_file(target)
            current = (inv.get("sha256") or "").strip()
            if current == actual:
                print(f"  {name}: already pinned, no change")
                continue
            text, ok = _replace_sha256_line(text, name, actual)
            if ok:
                shown = current[:16] if current else "<unpinned>"
                changes.append(f"{name}: sha256 {shown} -> {actual[:16]}...")
            else:
                print(f"  {name}: WARNING could not locate its sha256: line")

        elif inv["kind"] == "manifest":
            manifest_rel = inv.get("manifest")
            if not manifest_rel:
                continue
            manifest_path = root / manifest_rel
            try:
                revisions = dict(_load_manifest(manifest_path))
            except (FileNotFoundError, ConfigError):
                revisions = {}
            drifted = {
                rel: sha256_file(root / rel)
                for rel in revisions
                if (root / rel).is_file() and sha256_file(root / rel) != revisions[rel]
            }
            if drifted:
                revisions.update(drifted)
                changes.append(f"{manifest_rel}: re-hashed {len(drifted)} drifted entry(ies)")
            manifest_updates[manifest_rel] = revisions

    print("\nPlanned changes:")
    if not changes:
        print("  (none)")
    for change in changes:
        print(f"  - {change}")
    if not changes:
        return EXIT_OK

    if not assume_yes:
        if not sys.stdin.isatty():
            print("\nRefusing to write without --yes (not an interactive terminal).")
            return EXIT_OK
        answer = input("\nApply these changes? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted. No files written.")
            return EXIT_OK

    if text != original:
        config.write_text(text, encoding="utf-8")
        print(f"\nWrote {config} (comments preserved)")

    for manifest_rel, revisions in manifest_updates.items():
        manifest_path = root / manifest_rel
        original_manifest = (
            manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else ""
        )
        manifest_path.write_text(
            _rewrite_manifest_text(original_manifest, revisions), encoding="utf-8"
        )
        print(f"Wrote {manifest_path} ({len(revisions)} revision(s))")

    print("\nNote: --update re-hashes APPLIED revisions. It never adds new entries.")
    print("A migration becomes 'applied' when it is actually applied to the database.")
    return EXIT_OK


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LifeOS invariant checker")
    parser.add_argument("--invariant", help="check a single invariant by name")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--config", help="path to invariants.yaml")
    parser.add_argument("--root", help="repo root that config paths are relative to")
    parser.add_argument("--update", action="store_true", help="refresh hash pins")
    parser.add_argument("--yes", action="store_true", help="assume yes for --update")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    config = Path(args.config).resolve() if args.config else root / DEFAULT_CONFIG_NAME

    try:
        if args.update:
            return do_update(config, root, assume_yes=args.yes)
        config_data = load_yaml(config)
        invariants = config_data["invariants"]

        if args.invariant:
            selected = [i for i in invariants if i["name"] == args.invariant]
            if not selected:
                known = ", ".join(sorted(i["name"] for i in invariants))
                raise ConfigError(f"unknown invariant {args.invariant!r}; known: {known}")
            invariants = selected

        all_violations: list[dict[str, Any]] = []
        for inv in invariants:
            all_violations.extend(run_invariant(inv, root))

        ok = not all_violations
    except ConfigError as exc:
        if args.json:
            print(json.dumps({"ok": False, "checked": 0, "error": str(exc),
                              "violations": []}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - never let a crash look like a violation
        if args.json:
            print(json.dumps({"ok": False, "checked": 0,
                              "error": f"{type(exc).__name__}: {exc}", "violations": []}, indent=2))
        else:
            print(f"ERROR: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps({
            "ok": ok,
            "checked": len(invariants),
            "violations": all_violations,
        }, indent=2))
    else:
        by_inv: dict[str, list[dict[str, Any]]] = {}
        for v in all_violations:
            by_inv.setdefault(v["invariant"], []).append(v)
        for inv in invariants:
            name = inv["name"]
            hits = by_inv.get(name, [])
            if hits:
                print(f"FAIL: {name}")
                for v in hits:
                    line_info = f":{v['line']}" if v["line"] is not None else ""
                    print(f"  - {v['path']}{line_info} - {v['message']}")
            else:
                print(f"PASS: {name}")
        print(f"\n{len(invariants)} invariant(s) checked, {len(all_violations)} violation(s).")

    return EXIT_OK if ok else EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())
