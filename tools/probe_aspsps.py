#!/usr/bin/env python3
"""
Enable Banking ASPSP probe — answers "does Enable Banking support Revolut NL?"

This is research tooling for bead LifeOS-1, not part of the application.

Two subcommands:

    probe_aspsps.py keygen   # generate RSA key + self-signed cert for the EB Control Panel
    probe_aspsps.py probe    # sign a JWT and call GET /aspsps?country=NL

Requires: cryptography  (uv pip install cryptography)

WHY THIS EXISTS
---------------
The ASPSP list is the only authoritative answer to three gating questions:
  1. Is Rabobank available in PRODUCTION (not just sandbox)?
  2. Is Revolut available at all?
  3. Does any NL bank use a non-redirect auth method (a security concern)?

HOW TO GET AN app_id
--------------------
1. Sign up at https://enablebanking.com and open the Control Panel.
2. Create an app. Choose the SANDBOX environment (auto-activates, free, and the
   ASPSP list is queryable immediately — you do NOT need production for step 3).
3. Run `keygen` below. Upload the generated .crt to the app's certificate field.
4. The Control Panel shows your app_id (a UUID). This is the JWT `kid`.
5. Run `probe`.

You can run `probe` against SANDBOX first. Note the sandbox ASPSP list is NOT
the production list — the Revolut answer must be re-confirmed in production
later by activating production in "restricted mode" (link your own accounts).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit("Missing dependency. Install it with:\n    uv pip install cryptography\n"
             "or: pip install cryptography")

# ── Credential location ──────────────────────────────────────────────────────
# Credentials live OUTSIDE the git working tree, on purpose.
#
# HISTORY: this used to default to ./.eb-keys/ inside the repo. That was a
# design error. It put live credentials somewhere any tool or agent could
# `rm -rf`, and it made them look like disposable build artifacts. On
# 2026-09-30 that mistake destroyed a real keypair whose certificate had
# already been uploaded to Enable Banking — unrecoverable, because a private
# key cannot be regenerated. Never write credentials inside this repository.
#
# Override with EB_KEY_DIR only if you know why.
DEFAULT_KEY_DIR = Path.home() / ".config" / "lifeos" / "eb"
KEY_DIR = Path(os.environ.get("EB_KEY_DIR", DEFAULT_KEY_DIR))

# Explicit path, if the user passed --key. May not exist; that is fine.
KEY_PATH = Path(os.environ.get("EB_KEY", KEY_DIR / "private.key"))
CRT_PATH = Path(os.environ.get("EB_CRT", KEY_DIR / "public.crt"))
APP_ID_PATH = Path(os.environ.get("EB_APP_ID_FILE", KEY_DIR / "app_id"))

# Enable Banking saves browser-generated keys to ~/Downloads as "<app_id>.pem",
# so look there too. The filename is also the app_id.
DOWNLOADS = Path.home() / "Downloads"
# Legacy location from the old design. Read-only: we still find keys left there
# by an earlier version, but nothing is ever written there now.
LEGACY_REPO_KEY_DIR = Path(__file__).resolve().parent.parent / ".eb-keys"
SEARCH_DIRS = (KEY_DIR, DOWNLOADS, LEGACY_REPO_KEY_DIR, Path.home() / ".eb-keys")



def discover_key() -> Path | None:
    """Find the RSA private key without being told where it is."""
    candidates: list[Path] = []
    if KEY_PATH.exists():
        candidates.append(KEY_PATH)
    for d in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for pat in ("*.pem", "*.key", "private*", "*.crt"):
            candidates.extend(sorted(d.glob(pat)))
    for c in candidates:
        if not c.is_file():
            continue
        try:
            blob = c.read_bytes()
        except OSError:
            continue
        if b"PRIVATE KEY" in blob:   # matches PKCS#1 and PKCS#8 PEM headers
            return c
    return None


def discover_cert(near: Path | None = None) -> Path | None:
    """Find the self-signed certificate, preferring one sitting beside the key."""
    candidates: list[Path] = []
    if CRT_PATH.exists():
        candidates.append(CRT_PATH)
    dirs: list[Path] = [near.parent] if near else []
    dirs += list(SEARCH_DIRS)
    for d in dirs:
        if not d or not d.is_dir():
            continue
        for pat in ("*.crt", "*.cer", "*.pem", "certificate*"):
            candidates.extend(sorted(d.glob(pat)))
    for c in candidates:
        if not c.is_file():
            continue
        try:
            blob = c.read_bytes()
        except OSError:
            continue
        if b"BEGIN CERTIFICATE" in blob:
            return c
    return None


def app_id_from_filename(key: Path) -> str | None:
    """EB names the downloaded key "<app_id>.pem". Recover the UUID from it."""
    import re
    m = re.match(r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                 r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", key.stem)
    return m.group(1) if m else None


API_BASE = "https://api.enablebanking.com"

# Fields we care about, in report order.
INTERESTING = ("name", "country", "maximum_consent_validity", "auth_methods",
               "required_psu_headers", "beta", "psu_type")


# ─────────────────────────────── keygen ───────────────────────────────

def cmd_keygen(_args: argparse.Namespace) -> int:
    """Generate a 4096-bit RSA key and a self-signed certificate.

    Writes to ~/.config/lifeos/eb/ — OUTSIDE the git working tree. See the
    comment on DEFAULT_KEY_DIR for why that matters.

    Equivalent openssl commands, if you prefer:
        openssl genrsa -out private.key 4096
        openssl req -new -x509 -days 365 -key private.key -out public.crt \
                -subj "/CN=lifeos-enable-banking"
    (the -subj avoids an interactive prompt that the plain command triggers)
    """
    repo = Path(__file__).resolve().parent.parent
    if KEY_DIR.resolve() == (repo / ".eb-keys").resolve() or repo in KEY_DIR.resolve().parents:
        sys.exit(f"REFUSING to write credentials inside the repository: {KEY_DIR}\n"
                 f"  A private key cannot be regenerated. Keep it outside the working tree.\n"
                 f"  Unset EB_KEY_DIR to use the default: {DEFAULT_KEY_DIR}")

    if KEY_PATH.exists():
        print(f"ERROR: {KEY_PATH} already exists. Delete it first if you want to regenerate.")
        print("       Rotating a key invalidates the previously uploaded certificate,")
        print("       which can orphan an Enable Banking app. See SAFETY.md.")
        return 1

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(KEY_DIR, 0o700)

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    os.chmod(KEY_PATH, 0o600)

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "lifeos-enable-banking"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LifeOS (personal)"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)              # self-signed
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    CRT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    # Leave a note so a future human (or agent) does not mistake these for junk.
    (KEY_DIR / "README.txt").write_text(
        "Enable Banking credentials for LifeOS\n"
        "=====================================\n\n"
        f"private.key  RSA {key.key_size}-bit. Signs every JWT sent to the Enable Banking API.\n"
        "public.crt   self-signed certificate. THIS is what you upload to the\n"
        "             Enable Banking Control Panel. The private key is never uploaded.\n"
        "app_id       the UUID Enable Banking assigns when you upload the certificate.\n\n"
        "DO NOT DELETE THESE FILES. DO NOT COMMIT THEM.\n"
        "The private key cannot be regenerated. If you lose it, the certificate\n"
        "already uploaded to Enable Banking becomes useless and that app must be\n"
        "recreated from scratch. This happened once on 2026-09-30; see SAFETY.md.\n\n"
        "If Enable Banking's own browser flow generated these for you, they were\n"
        "also saved to ~/Downloads as <app_id>.pem.\n"
    )
    os.chmod(KEY_DIR / "README.txt", 0o600)

    print("Generated in", KEY_DIR)
    print(f"  private key   {KEY_PATH}   (mode 600)")
    print(f"  certificate   {CRT_PATH}")
    print(f"  readme        {KEY_DIR / 'README.txt'}")
    print()
    print("NEXT STEPS — do these in order:")
    print("  1. Upload public.crt to your app in the Enable Banking Control Panel.")
    print("  2. Enable Banking assigns an app_id (a UUID). RECORD IT NOW:")
    print(f"         echo -n 'PASTE_THE_UUID_HERE' > {APP_ID_PATH}")
    print("     This app_id is the only handle you have on that app. Without it you")
    print("     cannot find or delete the app in the Control Panel.")
    print("  3. Confirm:  python3 tools/probe_aspsps.py verify")
    print("  4. Then:     python3 tools/probe_aspsps.py probe")
    print()
    print("SECURITY: move the private key into your OS keyring once this works:")
    print(f"     secret-tool store --label='lifeos-eb-key' key pem < {KEY_PATH}")
    print("Keep a copy of the key OFF this machine. It is the only thing standing")
    print("between you and having to redo this.")
    return 0



# ──────────────────────────────── probe ────────────────────────────────

def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_jwt(app_id: str, key: rsa.RSAPrivateKey, ttl: int = 3600) -> str:
    """RS256 JWT. Note: RS256, not HS256 — the original assumption was wrong."""
    now = int(time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + ttl,
    }
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(payload, separators=(",", ":")).encode())
    ).encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + b64url(signature)


def load_app_id() -> str:
    if APP_ID_PATH.exists():
        v = APP_ID_PATH.read_text().strip()
        if v:
            return v
    env = os.environ.get("EB_APP_ID")
    if env and env.strip():
        return env.strip()
    # Last resort: EB names the downloaded key "<app_id>.pem".
    key = discover_key()
    if key:
        guess = app_id_from_filename(key)
        if guess:
            print(f"(app_id recovered from key filename: {guess})")
            return guess
    sys.exit(
        "No app_id found.\n"
        "  Do one of:\n"
        f"    echo -n 'the-uuid' > {APP_ID_PATH}\n"
        "    export EB_APP_ID='the-uuid'\n"
        "    python3 tools/probe_aspsps.py --app-id 'the-uuid' probe\n"
        "  The uuid is the app id shown in the Control Panel. If you generated the\n"
        "  key in the browser it is also the filename in ~/Downloads."
    )


def load_key() -> rsa.RSAPrivateKey:
    if KEY_PATH.exists():
        key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    else:
        found = discover_key()
        if found is None:
            sys.exit(
                "No private key found. Looked in:\n"
                f"  {KEY_PATH}\n"
                f"  {KEY_DIR}/*.pem, *.key, private*\n"
                f"  {DOWNLOADS}/*.pem, *.key, private*\n"
                "\nGenerate one with 'keygen', upload the .crt to your Enable Banking\n"
                "app, or point at an existing file with --key PATH."
            )
        print(f"(using discovered private key: {found})")
        key = serialization.load_pem_private_key(found.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        sys.exit(f"{KEY_PATH} is not an RSA private key (found {type(key).__name__}).\n"
                 f"  Enable Banking requires RSA. Re-generate with:\n"
                 f"    openssl genrsa -out private.key 4096")
    return key


def load_cert():
    if not CRT_PATH.exists():
        return None
    return x509.load_pem_x509_certificate(CRT_PATH.read_bytes())


# ─────────────────────────────── verify ───────────────────────────────

def cmd_verify(_args: argparse.Namespace) -> int:
    """Check that the private key and certificate exist, match, and are RSA.

    Run this before 'probe' — a mismatched pair is the most common cause of a
    401 from Enable Banking, and it is invisible until the API rejects you.
    """
    ok = True

    # Resolve by discovery so the common case (browser-downloaded key in
    # ~/Downloads) needs no arguments at all.
    found_key = KEY_PATH if KEY_PATH.exists() else discover_key()
    found_crt = discover_cert(found_key)

    print(f"private key : {found_key or '(not found)'}")
    print(f"certificate : {found_crt or '(not found)'}")
    try:
        shown_app_id = (APP_ID_PATH.read_text().strip() if APP_ID_PATH.exists()
                        else os.environ.get("EB_APP_ID")
                        or (app_id_from_filename(found_key) if found_key else None)
                        or "(not set)")
    except OSError:
        shown_app_id = "(not set)"
    print(f"app_id      : {shown_app_id}")
    print()

    key = None
    if found_key is None:
        print("FAIL  private key not found. Looked in:")
        for d in SEARCH_DIRS:
            print(f"        {d}/")
        print("      Generate one with 'keygen', or pass --key PATH.")
        ok = False
    else:
        try:
            key = load_key()
            mode = oct(found_key.stat().st_mode & 0o777)
            print(f"OK    private key loads, {key.key_size}-bit RSA  (mode {mode})")
            if key.key_size < 2048:
                print(f"FAIL  key is only {key.key_size} bits; Enable Banking wants 2048 or more")
                ok = False
            if mode not in ("0o600", "0o400"):
                print(f"WARN  key mode is {mode}; tighten with: chmod 600 {found_key}")
        except SystemExit as e:
            print(f"FAIL  {e}")
            return 1
        except Exception as e:
            print(f"FAIL  cannot read private key: {e}")
            return 1

    cert = None
    if found_crt is not None:
        try:
            cert = x509.load_pem_x509_certificate(found_crt.read_bytes())
        except Exception as e:
            print(f"FAIL  found {found_crt} but cannot parse it as a certificate: {e}")
            ok = False

    if cert is None:
        print("WARN  no certificate found — you cannot create the app without one")
        print("      For a browser-generated key, Enable Banking saves both the .pem and")
        print("      the .crt to ~/Downloads. Otherwise generate one with 'keygen'.")
    else:
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        self_signed = subject == issuer
        # cryptography >= 42 exposes *_utc properties; 41 only has naive ones.
        not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(
            tzinfo=dt.timezone.utc)
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(
            tzinfo=dt.timezone.utc)
        days_left = (not_after - dt.datetime.now(dt.timezone.utc)).days
        print(f"OK    certificate parses  subject={subject}")
        print(f"      self-signed: {self_signed}"
              f"{'' if self_signed else '   <-- EB expects a self-signed cert'}")
        print(f"      valid {not_before:%Y-%m-%d} -> {not_after:%Y-%m-%d}  ({days_left} days left)")
        if days_left < 0:
            print("FAIL  certificate has EXPIRED — upload a fresh one")
            ok = False
        elif days_left < 30:
            print("WARN  certificate expires soon")
        if not self_signed:
            ok = False

    # The decisive check: do key and cert correspond?
    if key and cert:
        if key.public_key().public_numbers() == cert.public_key().public_numbers():
            print("OK    private key matches the certificate (same public key)")
        else:
            print("FAIL  private key does NOT match the certificate.")
            print("      This is the #1 cause of a 401 from Enable Banking.")
            print("      Re-upload the certificate that matches this key, or use the other key.")
            ok = False

    if shown_app_id == "(not set)":
        print()
        print("NEXT  no app_id yet. Copy it from the Control Panel, then re-run with:")
        print(f"      python3 tools/probe_aspsps.py --app-id <uuid> probe")

    print()
    print("RESULT:", "ready to probe" if ok else "NOT ready - fix the FAILs above")
    return 0 if ok else 1


def http_get(url: str, token: str) -> tuple[int, bytes, dict]:
    """Minimal HTTPS GET. Uses urllib so this script has one dependency, not two."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def cmd_probe(args: argparse.Namespace) -> int:
    app_id = load_app_id()
    key = load_key()
    url = f"{API_BASE}/aspsps?country={args.country}&psu_type=personal"

    print(f"GET {url}")
    print(f"  app_id: {app_id}")
    print(f"  key   : {KEY_PATH}")
    print()

    token = build_jwt(app_id, key)
    status, body, _ = http_get(url, token)

    if status != 200:
        print(f"HTTP {status}")
        try:
            print(json.dumps(json.loads(body), indent=2)[:2000])
        except Exception:
            print(body[:2000].decode("utf-8", "replace"))
        print()
        if status in (401, 403):
            print("Likely causes:")
            print("  - The JWT is signed with a different key than the certificate you uploaded.")
            print("  - The app_id does not match the uploaded certificate.")
            print("  - The app is still 'pending' in the Control Panel.")
        return 1

    data = json.loads(body)
    aspsps = data if isinstance(data, list) else data.get("aspsps", [])

    print(f"HTTP 200 — {len(aspsps)} ASPSPs for {args.country}")
    print()

    wanted = ("rabobank", "revolut", "bunq", "ing", "abn amro", "abnamro",
              "asps bank", "sns bank", "regiobank", "knab", "triodos", "de Volksbank")

    def matches(a: dict) -> bool:
        name = (a.get("name") or "").lower()
        return any(w in name for w in wanted)

    print("=" * 78)
    print("  THE ANSWER")
    print("=" * 78)
    for label, probe_names in (("Rabobank", ("rabobank",)),
                               ("Revolut", ("revolut",))):
        hits = [a for a in aspsps
                if any(p in (a.get("name") or "").lower() for p in probe_names)]
        if hits:
            print(f"  {label:10} FOUND   -> {[h.get('name') for h in hits]}")
        else:
            print(f"  {label:10} ABSENT  -> not in this environment's list")
    print()

    interesting = [a for a in aspsps if matches(a)]
    if not interesting:
        interesting = aspsps[:8]

    for a in interesting:
        print("-" * 78)
        for field in INTERESTING:
            if field in a and a[field] not in (None, "", []):
                print(f"  {field:24} {a[field]}")
    print("-" * 78)

    # The two security-relevant checks.
    print()
    print("SECURITY CHECK — auth methods (a non-redirect method would need care):")
    for a in interesting:
        methods = a.get("auth_methods")
        if methods:
            non_redirect = [m for m in methods
                            if isinstance(m, str) and "redirect" not in m.lower()]
            flag = "  <-- CHECK THIS" if non_redirect else ""
            print(f"  {a.get('name'):24} {methods}{flag}")
    print()

    print("CONSENT VALIDITY (seconds; 15552000 = 180 days):")
    for a in interesting:
        v = a.get("maximum_consent_validity")
        if v:
            print(f"  {a.get('name'):24} {v:>10}  (~{int(v) / 86400:.0f} days)")
    print()

    out = Path(f"aspsps-{args.country}.json")
    out.write_text(json.dumps(aspsps, indent=2))
    print(f"Full list saved to {out}  (commit this to docs/research/ as evidence)")
    print()
    print("NEXT: record the answer in docs/research/01-enable-banking-psd2.md,")
    print("      update bead LifeOS-1, and close it.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--key", help="path to your existing RSA private key")
    p.add_argument("--crt", help="path to your existing certificate")
    p.add_argument("--app-id", help="app_id UUID, instead of writing it to a file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="generate RSA key + self-signed cert").set_defaults(fn=cmd_keygen)
    sub.add_parser("verify", help="check your key/cert pair before calling the API").set_defaults(fn=cmd_verify)
    probe = sub.add_parser("probe", help="call GET /aspsps and summarize")
    probe.add_argument("--country", default="NL")
    probe.set_defaults(fn=cmd_probe)

    args = p.parse_args()

    # These are global flags, so apply them before the subcommand runs.
    global KEY_PATH, CRT_PATH
    if args.key:
        KEY_PATH = Path(args.key)
    if args.crt:
        CRT_PATH = Path(args.crt)
    if args.app_id:
        os.environ["EB_APP_ID"] = args.app_id

    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
