#!/usr/bin/env python3
"""
Destructive-action guard.

Wraps `rm` for anything touching the LifeOS working tree. Default is DRY RUN:
it prints what would be removed and refuses to do it.

Why this exists
---------------
On 2026-09-30 an agent ran a bare `rm -rf .eb-keys/` on the assumption the
directory held only its own test artifacts. It actually held a live
Enable Banking private key whose certificate had already been uploaded to the
Control Panel. The key was unrecoverable, because a private key cannot be
regenerated, and the app was orphaned. See ../SAFETY.md.

The failure was not `rm`. It was deleting files the agent had not created and
had not verified the provenance of.

Usage
-----
    python3 tools/rm_guard.py <path> [<path> ...]          # dry run, always safe
    python3 tools/rm_guard.py --force <path> [...]         # actually remove
    python3 tools/rm_guard.py --force --i-created-this-session <path>

Rules enforced
--------------
1. Dry run by default. You must pass --force to delete.
2. Refuses to remove the repository root, the git directory, or $HOME.
3. Refuses any target outside the working tree. This tool governs the repo only.
4. Refuses to remove anything inside the tree that looks like a credential
   (private.key, *.pem, *.crt, app_id, id_rsa, ...), even with --force.
5. Always prints a manifest of every file before removing anything.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPO = REPO.resolve()

# Paths that must never be removed, whatever the flags say.
FORBIDDEN = {
    REPO,
    REPO / ".git",
    Path.home(),
    Path("/"),
}

CREDENTIAL_HINTS = (".key", ".pem", ".crt", ".cer", "app_id", "id_rsa", "id_ed25519",
                    "private", "credentials")


def looks_like_credential(p: Path) -> bool:
    """True if the filename looks like a secret.

    NOTE: the hint list already carries the leading dot ('.key'), so this must
    compare against the suffix directly - building f".{h}" would yield '..key'
    and silently match nothing. That bug shipped once already; the tests cover it.
    """
    if p.is_dir():
        return False
    name = p.name.lower()
    suffix = p.suffix.lower()
    for hint in CREDENTIAL_HINTS:
        if name == hint or name.endswith(hint):
            return True
        if hint.startswith(".") and suffix == hint:
            return True
    return False




def manifest(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(x for x in target.rglob("*") if x.is_file())
    return [target]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--force", action="store_true",
                    help="actually remove (default is a dry run)")
    args = ap.parse_args()

    targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        p = (p if p.is_absolute() else (REPO / p)).resolve()
        targets.append(p)

    # ── Safety checks ───────────────────────────────────────────────────────
    blocked: list[str] = []
    for p in targets:
        if p in FORBIDDEN:
            blocked.append(f"{p} is a protected path (repo root, .git, or home)")
        elif not (p == REPO or REPO in p.parents):
            blocked.append(
                f"{p} is outside the working tree {REPO}. This guard only governs the\n"
                f"        repository; use ordinary care for paths elsewhere.")

    creds = [f for t in targets for f in manifest(t) if looks_like_credential(f)]
    if creds:
        blocked.append(
            f"{len(creds)} file(s) look like credentials and are inside the working tree: "
            + ", ".join(str(c) for c in creds[:5])
            + "\n        A private key cannot be regenerated. If these are live credentials, "
              "STOP.\n        If you created them this session and read them, delete them by hand "
              "deliberately.")

    print("=" * 72)
    print("  REMOVE MANIFEST")
    print("=" * 72)
    total = 0
    for t in targets:
        if t not in targets:  # pragma: no cover
            continue
        if not t.exists():
            print(f"\n{t}\n  (does not exist)")
            continue
        files = manifest(t)
        total += len(files)
        kind = "dir " if t.is_dir() else "file"
        print(f"\n{kind} {t}   -> {len(files)} file(s)")
        for f in files[:40]:
            print(f"    {f}")
        if len(files) > 40:
            print(f"    ... and {len(files) - 40} more")
    print()
    print(f"TOTAL: {total} file(s) across {len(targets)} target(s)")

    if blocked:
        print()
        print("=" * 72)
        print("  BLOCKED")
        print("=" * 72)
        for b in blocked:
            print(f"  - {b}")
        print()
        print("Nothing was removed. Read SAFETY.md if you believe this is wrong.")
        return 2

    if not args.force:
        print()
        print("DRY RUN — nothing was removed.")
        print("Re-run with --force to actually delete.")
        return 1

    print()
    print("--force given. Removing...")
    for t in targets:
        if not t.exists():
            continue
        if t.is_dir():
            shutil.rmtree(t)
            print(f"  removed dir  {t}")
        else:
            t.unlink()
            print(f"  removed file {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
