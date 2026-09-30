#!/usr/bin/env python3
"""Repair beads created by the buggy shell script: clear bogus assignee,
set correct acceptance criteria, and wire dependencies."""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The -a flag was --assignee, so acceptance text landed there for 1-6 and 13.
# Acceptance text is recoverable from create_beads.py; assignee is just cleared.
SCRIPT = REPO / "tools" / "create_beads.py"
DEPS = {
    "LifeOS-1": [], "LifeOS-2": [], "LifeOS-3": [], "LifeOS-4": [],
    "LifeOS-5": ["LifeOS-3"], "LifeOS-6": ["LifeOS-5"],
    "LifeOS-7": ["LifeOS-6", "LifeOS-2"], "LifeOS-8": ["LifeOS-7"],
    "LifeOS-9": ["LifeOS-6"], "LifeOS-10": ["LifeOS-8", "LifeOS-9", "LifeOS-1"],
    "LifeOS-11": ["LifeOS-7"], "LifeOS-12": ["LifeOS-11", "LifeOS-2"],
    "LifeOS-13": ["LifeOS-1"], "LifeOS-14": ["LifeOS-8"], "LifeOS-15": ["LifeOS-9"],
}


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True)


def show(tid):
    r = run(["bd", "show", tid, "--json"])
    if r.returncode != 0:
        return None
    d = json.loads(r.stdout)
    return d[0] if isinstance(d, list) else d


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cb", SCRIPT)
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    wanted = {b["id"]: b for b in cb.BEADS}

    problems = []
    for tid, deps in DEPS.items():
        cur = show(tid)
        if cur is None:
            problems.append(f"{tid}: missing")
            continue
        w = wanted[tid]
        args = ["bd", "update", tid, "--title", w["title"],
                "--description", w["description"],
                "--acceptance", w["acceptance"],
                "--design", w["design"],
                "-p", str(w["p"])]
        r = run(args)
        if r.returncode != 0:
            problems.append(f"{tid}: {r.stderr.strip() or r.stdout.strip()}")
            continue
        # Clear the polluted assignee field.
        run(["bd", "update", tid, "--assignee", ""])

    print("repaired", len(DEPS) - len(problems), "of", len(DEPS))
    for p in problems:
        print("  PROBLEM:", p)

    print("\n--- final board ---")
    r = run(["bd", "list", "--json"])
    iss = json.loads(r.stdout)
    iss = iss if isinstance(iss, list) else iss.get("issues", [])
    rows = []
    for i in iss:
        if i["status"] == "closed":
            continue
        n = int(i["id"].split("-")[1])
        blocked = [d for d in DEPS.get(i["id"], []) if True]
        rows.append((n, i))
    for n, i in sorted(rows):
        deps = DEPS.get(i["id"], [])
        d = f"  <- {','.join(deps)}" if deps else ""
        print(f'{i["id"]:12} P{i["priority"]} {i["issue_type"]:8} {i["title"][:66]}{d}')


if __name__ == "__main__":
    main()
