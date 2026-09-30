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

KEY_DIR = Path(os.environ.get("EB_KEY_DIR", "./.eb-keys"))
KEY_PATH = KEY_DIR / "private.key"
CRT_PATH = KEY_DIR / "public.crt"
APP_ID_PATH = KEY_DIR / "app_id"

API_BASE = "https://api.enablebanking.com"

# Fields we care about, in report order.
INTERESTING = ("name", "country", "maximum_consent_validity", "auth_methods",
               "required_psu_headers", "beta", "psu_type")


# ─────────────────────────────── keygen ───────────────────────────────

def cmd_keygen(_args: argparse.Namespace) -> int:
    """Generate a 4096-bit RSA key and a self-signed certificate.

    Equivalent openssl commands, if you prefer:
        openssl genrsa -out private.key 4096
        openssl req -new -x509 -days 365 -key private.key -out public.crt
    """
    if KEY_PATH.exists():
        print(f"ERROR: {KEY_PATH} already exists. Delete it first if you want to regenerate.")
        print("       Rotating a key invalidates the previously uploaded certificate.")
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

    print("Generated:")
    print(f"  private key : {KEY_PATH}   (mode 600, NEVER commit this)")
    print(f"  certificate : {CRT_PATH}")
    print()
    print("Next:")
    print("  1. Upload the .crt to your app in the Enable Banking Control Panel.")
    print("  2. Copy the app_id (UUID) into " + str(APP_ID_PATH))
    print("  3. Run:  probe_aspsps.py probe")
    print()
    print("SECURITY: once this works, move the private key into your OS keyring:")
    print("     secret-tool store --label='lifeos-eb-key' key pem < private.key")
    print("and delete the file. .gitignore already excludes *.key and *.pem, but do not rely on that alone.")
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
        return APP_ID_PATH.read_text().strip()
    env = os.environ.get("EB_APP_ID")
    if env:
        return env.strip()
    sys.exit(f"No app_id found. Put it in {APP_ID_PATH}, or set EB_APP_ID.")


def load_key() -> rsa.RSAPrivateKey:
    if not KEY_PATH.exists():
        sys.exit(f"No private key at {KEY_PATH}. Run 'keygen' first.")
    key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        sys.exit("Expected an RSA private key.")
    return key


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
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="generate RSA key + self-signed cert").set_defaults(fn=cmd_keygen)
    probe = sub.add_parser("probe", help="call GET /aspsps and summarize")
    probe.add_argument("--country", default="NL")
    probe.set_defaults(fn=cmd_probe)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
