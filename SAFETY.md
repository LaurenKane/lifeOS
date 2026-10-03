# SAFETY.md — rules for touching this repository

**Read this before running any destructive command.** It exists because of a real, unrecoverable
mistake. Not a hypothetical.

---

## The incident (2026-09-30)

An agent working on bead `LifeOS-1` ran:

```bash
rm -rf .eb-keys/
```

on the assumption that the directory contained only throwaway test artifacts the agent had created.

**It did not.** It contained a live Enable Banking RSA private key and its self-signed certificate.
The certificate had already been uploaded to the Enable Banking Control Panel.

**Consequences:**

- The private key was **permanently lost**. A private key cannot be regenerated — that is the entire
  point of it. No git history, no backup, no trash. `rm` does not use a trash directory.
- The Enable Banking app that certificate belonged to (`6afaac81-877b-40d8-a2ad-554b450f23e0`) could
  no longer be used, because a PSD2 app has authority only via the key that signs its JWTs.
- The user had not recorded the `app_id` anywhere else, so they could not locate the app in the
  Control Panel to delete it.

**Mitigating factor:** the app was inert. No bank account had ever been connected, no consent flow was
completed, and with the private key destroyed nobody could use it. There was no data exposure. It was a
clumsy, avoidable loss of time — not a security incident.

---

## Root causes, and the rule for each

### 1. Deleting files whose provenance you did not verify

The agent never established that it had created those files. It *assumed* them to be its own test
output. The `.gitignore` entry was used to justify this, which was a category error: **gitignore means
*untracked*, not *mine*.** In fact gitignoring them is exactly why there was no recovery path.

> **Rule 1 — Never delete a file you did not create in the current session, and whose contents you have
> not read in this session.** If you cannot state who created it and when, it is not yours to delete.
> Say so out loud instead of guessing.

### 2. Credentials had a default location inside the working tree

`tools/probe_aspsps.py keygen` wrote to `./.eb-keys/`, inside the repo. That made live credentials look
like disposable build output, sitting exactly where a cleanup step would hit them.

> **Rule 2 — Credentials never live inside a working tree.** They default to
> `~/.config/lifeos/`, outside the repository. `keygen` now **refuses** to write inside the repo.

### 3. `rm -rf` was run with no manifest

A destructive command was issued blind, so nobody could see what it was about to destroy.

> **Rule 3 — Use `tools/rm_guard.py`, never a bare `rm`.** It is a dry run by default, prints a manifest
> of every file it would delete, refuses protected paths and out-of-tree targets, and blocks credential
> deletion unless you explicitly assert `--i-created-this-session`.

### 4. The `app_id` was not recorded at the moment it was issued

The `app_id` is the only handle you have on an Enable Banking app. It lived solely in a file that was
deleted.

> **Rule 4 — Record external identifiers the moment they are issued, in at least two places, one of them
> outside the repo.** `keygen` now prints the exact command to write it to
> `~/.config/lifeos/eb/app_id`.

---

## Hard rules

1. **Never `rm -rf` in this working tree.** Use `python3 tools/rm_guard.py <path>`.
2. **Never delete a file you did not create this session without reading it first.** Provenance before
   deletion, always.
3. **Never write credentials, secrets, or keypairs inside this repository.** They live in
   `~/.config/lifeos/`.
4. **Never run a bulk glob delete** (`rm -rf dir/*`, `find -delete`, `git clean -fd`) inside the working
   tree. `git clean -n` to preview at minimum.
5. **Before any destructive action, state in your reply exactly what you are about to delete and why**,
   and wait for confirmation if there is any doubt about provenance.
6. **Backups are not a substitute for caution.** A credential that exists in exactly one place is one
   mistaken command from gone. Keep an off-machine copy of the Enable Banking private key.

---

## Recovery, if a credential is ever lost again

1. **Assess exposure first.** A PSD2 app whose private key is gone is **inert** — no one can sign
   requests as it. If no consent flow was ever completed, no bank account is connected and there is
   nothing to clean up.
2. **Recover the `app_id`** from this repository's history, logs, or the Control Panel's own app list.
3. **Recreate the app**: `keygen` → upload the new certificate → record the new `app_id`.
4. **Do not attempt to recover the old key.** It is not possible. Move on.
5. Only if a consent flow *had* been completed: revoke consent at the bank, and use
   `DELETE /sessions/{session_id}` on Enable Banking.

---

## Related

- Bead `LifeOS-16` — this incident and the fixes.
- `tools/rm_guard.py` — the guard mandated by Rule 3.
- `tools/probe_aspsps.py` — `keygen` refuses to write inside the repo (Rule 2).
- `docs/ENABLE-BANKING-SETUP.md` — the live credential workflow: where the key and the
  `app_id` live, and how to record them the moment they are issued. Its
  **"Credential inventory & rotation"** section is the authoritative list of what must be
  kept alive (RSA key, app_id, session_id, refresh token), where each is stored, and the
  revocation/rotation procedure if one is lost.
