#!/usr/bin/env bash
#
# Negative tests for scripts/check_invariants.py.
#
# WHY THIS EXISTS
# A checker that always exits 0 is indistinguishable from a checker that works,
# and both look identical in CI: green. So this script proves the checker
# actually rejects each thing it claims to forbid, by feeding it a deliberately
# broken COPY of the repository and asserting the exact exit code and the
# invariant name it reports.
#
# Two directions, not one. Each regex invariant is also checked against a
# LEGAL statement that must NOT be flagged: `UPDATE source_record SET status`
# for raw_data_immutable, and an unqualified `REFERENCES account(id)` for
# no_cross_schema_fk. An over-broad pattern fails these, and the usual response
# to an over-broad pattern is to weaken it until the real violation gets
# through. Testing only the positive direction hides that.
#
# `set -e` is deliberately NOT used. Most cases here run a command that is
# SUPPOSED to fail, and `set -e` would abort on the first one. Failures are
# counted explicitly and the script exits 1 if any case misbehaved.
#
# DELETION SAFETY (SAFETY.md)
# Every file removed below was created by this script, moments earlier, inside a
# `mktemp -d` directory outside the repository. Deletions are explicit named
# files plus `rmdir`, which refuses to remove a non-empty directory. There is
# no `rm -rf`, no glob, and no path outside the temporary trees.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECKER="${REPO_ROOT}/scripts/check_invariants.py"
PYTHON="${PYTHON:-python3}"

pass=0
fail=0

note() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '[PASS] %s\n' "$*"; pass=$((pass + 1)); }
bad()  { printf '[FAIL] %s\n' "$*"; fail=$((fail + 1)); }

FINGERPRINT_REL="backend/finance/ingestion/fingerprint.py"
LEDGER_REL="scripts/migrations.lock.json"

# A fresh throwaway tree: the real invariants.yaml, the real empty migration
# manifest, and a real copy of fingerprint.py so the hash invariant has
# something to hash.
make_tree() {
  local root
  root="$(mktemp -d)"
  mkdir -p "${root}/backend/finance/ingestion" "${root}/scripts" \
           "${root}/backend/finance/alembic/versions" \
           "${root}/backend/core/alembic/versions"
  cp "${REPO_ROOT}/invariants.yaml" "${root}/invariants.yaml"
  cp "${REPO_ROOT}/${LEDGER_REL}" "${root}/${LEDGER_REL}"
  cp "${REPO_ROOT}/${FINGERPRINT_REL}" "${root}/${FINGERPRINT_REL}"
  printf '%s' "${root}"
}

# Removes only what make_tree and the cases below put there. `rmdir -p` walks
# upward and refuses anything non-empty, so it cannot escape into the repo.
cleanup_tree() {
  local root="$1"
  find "${root}" -type f -delete 2>/dev/null
  find "${root}" -depth -type d -empty -delete 2>/dev/null
  [ -e "${root}" ] && { echo "WARNING: could not fully remove ${root}" >&2; return 1; }
  return 0
}

# run_case <label> <tree> <invariant> <expected-exit>
#
# Also asserts that a rejecting run actually NAMES the expected invariant. An
# exit 1 caused by some unrelated internal error would otherwise be accepted as
# a pass, which is the same vacuous-check problem this script exists to catch.
run_case() {
  local label="$1" root="$2" invariant="$3" expected="$4" out rc
  out="$("${PYTHON}" "${CHECKER}" --root "${root}" --config "${root}/invariants.yaml" \
          --invariant "${invariant}" 2>&1)"
  rc=$?

  if [ "${rc}" -ne "${expected}" ]; then
    bad "${label}: expected exit ${expected}, got ${rc}"
    printf '       output:\n%s\n' "${out}" | sed 's/^/       /'
    return 1
  fi
  if [ "${expected}" -eq 1 ] && ! printf '%s' "${out}" | grep -q "${invariant}"; then
    bad "${label}: exited 1 but never named ${invariant}"
    printf '       output:\n%s\n' "${out}" | sed 's/^/       /'
    return 1
  fi
  ok "${label} (exit ${rc})"
  return 0
}

# ---------------------------------------------------------------------------
note "control: an untouched copy must PASS"
# ---------------------------------------------------------------------------
tree="$(make_tree)"
run_case "clean tree passes fingerprint_frozen" "${tree}" fingerprint_frozen 0
cleanup_tree "${tree}"

# ---------------------------------------------------------------------------
note "raw_data_immutable"
# ---------------------------------------------------------------------------
tree="$(make_tree)"
cat > "${tree}/backend/finance/rogue_update.py" <<'PY'
# The violation: assigning raw_data is exactly what the invariant forbids.
UPDATE source_record SET raw_data = '{}' WHERE id = 1
PY
run_case "UPDATE ... SET raw_data is rejected" "${tree}" raw_data_immutable 1
cleanup_tree "${tree}"

tree="$(make_tree)"
cat > "${tree}/backend/finance/legal_update.py" <<'PY'
# Legal: touches `status`, never raw_data or raw_description.
UPDATE source_record SET status = 'booked' WHERE id = 1
PY
run_case "UPDATE ... SET status is allowed" "${tree}" raw_data_immutable 0
cleanup_tree "${tree}"

tree="$(make_tree)"
cat > "${tree}/backend/finance/rogue_delete.py" <<'PY'
DELETE FROM source_record WHERE id = 1
PY
run_case "DELETE FROM source_record is rejected" "${tree}" raw_data_immutable 1
cleanup_tree "${tree}"

# ---------------------------------------------------------------------------
note "no_cross_schema_fk"
# ---------------------------------------------------------------------------
tree="$(make_tree)"
cat > "${tree}/backend/finance/rogue_fk.py" <<'PY'
revision = """CREATE TABLE finance.orphan (
    id bigint PRIMARY KEY REFERENCES health.account(id)
);"""
PY
run_case "REFERENCES health.account (bare) is rejected" "${tree}" no_cross_schema_fk 1
cleanup_tree "${tree}"

tree="$(make_tree)"
cat > "${tree}/backend/finance/rogue_fk_quoted.py" <<'PY'
revision = """CREATE TABLE finance.orphan (
    id bigint PRIMARY KEY REFERENCES "health".account(id)
);"""
PY
run_case 'REFERENCES "health".account (quoted) is rejected' "${tree}" no_cross_schema_fk 1
cleanup_tree "${tree}"

tree="$(make_tree)"
cat > "${tree}/backend/finance/legal_fk.py" <<'PY'
revision = """CREATE TABLE finance.entry (
    id bigint PRIMARY KEY,
    account_id bigint REFERENCES account(id)
);"""
PY
run_case "unqualified REFERENCES account is allowed" "${tree}" no_cross_schema_fk 0
cleanup_tree "${tree}"

tree="$(make_tree)"
cat > "${tree}/backend/finance/legal_public_fk.py" <<'PY'
revision = """CREATE TABLE finance.entry (
    id bigint PRIMARY KEY,
    account_id bigint REFERENCES public.account(id)
);"""
PY
run_case "REFERENCES public.account is allowed" "${tree}" no_cross_schema_fk 0
cleanup_tree "${tree}"

# ---------------------------------------------------------------------------
note "fingerprint_frozen"
# ---------------------------------------------------------------------------
tree="$(make_tree)"
# One appended comment line. A pin that survives a whitespace change is not a
# pin, and a silent edit to the fingerprint is the exact failure replay depends
# on not happening.
printf '\n# an innocuous-looking comment\n' >> "${tree}/${FINGERPRINT_REL}"
run_case "appending a comment to fingerprint.py is rejected" "${tree}" fingerprint_frozen 1
cleanup_tree "${tree}"

tree="$(make_tree)"
printf '"""Frozen but rewritten."""\n' > "${tree}/${FINGERPRINT_REL}"
run_case "replacing fingerprint.py wholesale is rejected" "${tree}" fingerprint_frozen 1
cleanup_tree "${tree}"

# ---------------------------------------------------------------------------
note "migrations_immutable"
# ---------------------------------------------------------------------------
# The real manifest is empty in M0 because no revision is applied yet —
# fabricating entries there would be dishonest. This case proves the check is
# wired by seeding a THROWAWAY manifest with a revision whose recorded hash
# cannot match.
tree="$(make_tree)"
cat > "${tree}/backend/finance/alembic/versions/0001_fake.py" <<'PY'
"""Fabricated applied revision. Used only by this negative test."""
revision = "0001_fake"
PY
cat > "${tree}/${LEDGER_REL}" <<'JSON'
{
  "revisions": {
    "0001_fake": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
JSON
run_case "edited applied revision is rejected" "${tree}" migrations_immutable 1
cleanup_tree "${tree}"

# ---------------------------------------------------------------------------
note "the three exit codes are distinguishable"
# ---------------------------------------------------------------------------
# Exit 2 for a config error, so a typo in a CI job cannot be mistaken for a
# clean policy check. Exit 1 for a real violation. Exit 0 for a pass.
out="$("${PYTHON}" "${CHECKER}" --root "${REPO_ROOT}" --invariant no_such_invariant 2>&1)"
rc=$?
if [ "${rc}" -eq 2 ]; then
  ok "unknown invariant name exits 2 (config error)"
else
  bad "unknown invariant exited ${rc}; expected 2"
  printf '       output:\n%s\n' "${out}" | sed 's/^/       /'
fi

out="$("${PYTHON}" "${CHECKER}" --root "${REPO_ROOT}" 2>&1)"
rc=$?
if [ "${rc}" -eq 0 ]; then
  ok "the real repository passes every invariant"
else
  bad "the real repository FAILED the invariant check (exit ${rc})"
  printf '       output:\n%s\n' "${out}" | sed 's/^/       /'
fi

# ---------------------------------------------------------------------------
printf '\n\033[1m%d passed, %d failed\033[0m\n' "${pass}" "${fail}"
[ "${fail}" -eq 0 ] || exit 1