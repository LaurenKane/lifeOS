#!/usr/bin/env python3
"""Parse American Express NL monthly card statements (PDF text layer) into JSONL.

Format notes, verified against the four supplied statements (dated 23.06 .. 23.09.2026,
covering periods 24.05-23.06 through 24.08-23.09):

* Dutch "Maandafrekening" for The Gold Card. Each statement is 4 pages and repeats a
  large fixed legal/footer block ("American Express Europe S.A. gevestigd ...") plus a
  "Nieuwe transacties voor:" sub-header. Those are not transactions and must be skipped.
* Every row carries TWO dates: ``Transactiedatum`` and ``Datum verwerkt`` (processing).
  They differ on roughly a third of rows, so both are captured and the pair is preserved
  rather than collapsed to one.
* A credit is marked by a line containing only ``CR`` printed *beneath* the amount, not
  by a sign in the amount itself. The amount column holds charges and credits as positive
  numbers alike, so the CR marker is the only way to tell them apart. This matters because
  a single statement's credit section mixes the monthly card payment with genuine
  refunds, and the two must not be conflated.
* The ``Bedrag in vreemde valuta`` column exists but is empty in all four statements, so
  no foreign-currency amount or rate is available from this export.
* Amounts are Dutch format: dot thousands, comma decimal.
* The statement date is the filename date; the *period* is printed separately as
  "Periode: 24.05.2026 tot 23.06.2026".

Sign convention: money leaving the card account is negative, money arriving positive.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

AMOUNT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])")
ROW_RE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{2})\s+(\d{2}\.\d{2}\.\d{2})\s+(.*)$")
CR_RE = re.compile(r"^\s*CR\s*$")
PERIOD_RE = re.compile(r"Periode:\s*(\d{2}\.\d{2}\.\d{4})\s+tot\s+(\d{2}\.\d{2}\.\d{4})")
CARD_RE = re.compile(r"Kaartnummer\s+([xX*\-]*\d+)")
STMT_DATE_RE = re.compile(r"^\s*23\.(\d{2})\.(\d{4})\s", re.M)

SKIP_MARKERS = (
    "American Express Europe S.A. gevestigd",
    "Nieuwe transacties voor:",
    "Is het adres onjuist?",
    "Het te betalen bedrag is met",
)

SUMMARY_LABELS = ("Vorig saldo", "Crediteringen", "Debiteringen", "Nieuw saldo")


def to_minor(text: str) -> int:
    s = text.replace(".", "").replace(",", "")
    if not s.isdigit():
        raise ValueError(f"unparseable Dutch amount: {text!r}")
    return int(s)


def mask_card(last_digits: str) -> str:
    return "****" + last_digits


def fingerprint(account_ref: str, date: str, amount_minor: int, currency: str, desc: str) -> str:
    norm = re.sub(r"\s+", " ", desc).strip().upper()
    return hashlib.sha256(
        f"amex|{account_ref}|{date}|{amount_minor}|{currency}|{norm}".encode()
    ).hexdigest()


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\b\d{6,}\*+\d{4}\b", "****", text)


def extract_text(pdf: Path) -> str:
    r = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                       capture_output=True, text=True, check=True)
    return r.stdout


def parse_summary(lines: list[str]) -> dict | None:
    """The five summary values sit on one line, four lines below the label row."""
    for i, line in enumerate(lines):
        if all(lbl in line for lbl in SUMMARY_LABELS):
            for j in range(i + 1, min(i + 8, len(lines))):
                nums = re.findall(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])", lines[j])
                if len(nums) >= 5:
                    return {
                        "previous_balance_minor": to_minor(nums[0]),
                        "credits_minor": to_minor(nums[1]),
                        "charges_minor": to_minor(nums[2]),
                        "new_balance_minor": to_minor(nums[3]),
                        "amount_due_minor": to_minor(nums[4]),
                        "summary_line": j + 1,
                    }
    return None


def expand(dmy: str, year: int) -> str:
    d, m, _ = dmy.split(".")
    return f"{year:04d}-{int(m):02d}-{int(d):02d}"


def parse(pdf: Path) -> dict:
    lines = extract_text(pdf).split("\n")
    text = "\n".join(lines)

    period = PERIOD_RE.search(text)
    period_from, period_to = (period.group(1), period.group(2)) if period else (None, None)
    year = int(period_to.split(".")[-1]) if period_to else 2026

    card = CARD_RE.search(text)
    card_last = card.group(1)[-4:] if card else None

    summary = parse_summary(lines)

    rows: list[dict] = []
    unparsed: list[dict] = []
    for idx, line in enumerate(lines):
        if any(m in line for m in SKIP_MARKERS):
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        txn_dmy, proc_dmy, rest = m.groups()
        amts = AMOUNT_RE.findall(line)
        if not amts:
            unparsed.append({"line_no": idx + 1, "text": line.strip()[:120],
                             "reason": "row shape matched but no amount found"})
            continue
        amount_raw = amts[-1]
        end = line.rfind(amount_raw)
        # Start from the description group, not m.end(): the row regex ends with a
        # catch-all group that already consumed the rest of the line, so slicing from
        # m.end() would always yield an empty description.
        desc = re.sub(r"\s{2,}", " ", line[m.start(3):end]).strip()
        # The credit marker is a separate line immediately below the amount, so it has to
        # be read with 0-based indexing to line up with the current row.
        is_credit = idx + 1 < len(lines) and bool(CR_RE.match(lines[idx + 1]))
        amount = to_minor(amount_raw)
        rows.append({
            "txn_date": expand(txn_dmy, year),
            "posting_date": expand(proc_dmy, year),
            "description": redact(desc),
            "amount_raw": amount_raw,
            "is_credit": is_credit,
            "signed_minor": amount if is_credit else -amount,
            "line_no": idx + 1,
        })

    return {
        "period_from": period_from, "period_to": period_to,
        "card_last": card_last, "summary": summary, "rows": rows,
        "unparsed": unparsed, "total_lines": len(lines),
    }


def build_rows(res: dict, pdf: Path) -> list[dict]:
    ref = mask_card(res["card_last"]) if res["card_last"] else None
    out = []
    for r in res["rows"]:
        is_payment = bool(re.search(r"HARTELIJK BEDANKT VOOR UW BETALING",
                                    r["description"] or "", re.I))
        extras = {
            "is_credit": r["is_credit"],
            "is_card_payment": is_payment,
            "dates_differ": r["txn_date"] != r["posting_date"],
            "foreign_amount_minor": None,
        }
        out.append({
            "source_file": pdf.name,
            "provider": "amex",
            "account_ref": ref,
            "raw_date": r["txn_date"],
            "raw_posting_date": r["posting_date"],
            "raw_description": r["description"],
            "raw_amount_minor": r["signed_minor"],
            "raw_currency": "EUR",
            "provider_txn_id": None,
            "fingerprint": fingerprint(ref, r["txn_date"], r["signed_minor"],
                                       "EUR", r["description"] or ""),
            "extras": extras,
        })
    return out


def selftest(res: dict, rows: list[dict]) -> dict:
    s = res["summary"]
    checks: list[dict] = []
    info: list[dict] = []

    def add(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    charges = -sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] < 0)
    credits = sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] > 0)
    if s:
        add("extracted charges == stated Debiteringen",
            charges == s["charges_minor"],
            f"extracted={charges} stated={s['charges_minor']} delta={charges - s['charges_minor']}")
        add("extracted credits == stated Crediteringen",
            credits == s["credits_minor"],
            f"extracted={credits} stated={s['credits_minor']} delta={credits - s['credits_minor']}")
        # On a card statement a charge increases the amount owed, so the identity runs
        # Vorig + Debiteringen - Crediteringen. Verified against all four statements;
        # the opposite sign does not hold for any of them.
        add("Vorig saldo + Debiteringen - Crediteringen == Nieuw saldo",
            s["previous_balance_minor"] + s["charges_minor"] - s["credits_minor"]
            == s["new_balance_minor"],
            f"{s['previous_balance_minor']} + {s['charges_minor']} - {s['credits_minor']} = "
            f"{s['previous_balance_minor'] + s['charges_minor'] - s['credits_minor']} "
            f"vs Nieuw saldo {s['new_balance_minor']}")
        add("Nieuw saldo == Te betalen",
            s["new_balance_minor"] == s["amount_due_minor"],
            f"{s['new_balance_minor']} vs {s['amount_due_minor']}")

    if res["period_from"] and res["period_to"]:
        lo, hi = (expand(res["period_from"], 2026), expand(res["period_to"], 2026))
        out_of_period = [
            {"date": r["raw_date"], "description": r["raw_description"]}
            for r in rows if not (lo <= r["raw_date"] <= hi)
        ]
        # Not a pass/fail check: the printed Periode is advisory, not a filter. Real
        # statements carry transactions dated the day before the period starts, so
        # rejecting them on period bounds would drop genuine spend.
        info.append({
            "check": "transactions outside the printed Periode (informational)",
            "pass": None,
            "detail": f"period={lo}..{hi} count={len(out_of_period)} "
                      f"rows={out_of_period[:5]}",
        })

    seen: dict[tuple, int] = {}
    for r in rows:
        k = (r["raw_date"], r["raw_amount_minor"], r["raw_description"])
        seen[k] = seen.get(k, 0) + 1
    identical = [{"key": list(k), "count": v} for k, v in seen.items() if v > 1]
    info.append({
        "check": "identical (date, amount, description) rows within this statement",
        "pass": None,
        "detail": f"count={len(identical)} rows={identical[:5]}",
    })

    differ = sum(1 for r in rows if r["extras"]["dates_differ"])
    return {"pass": all(c["pass"] for c in checks), "checks": checks,
            "informational": info,
            "rows_with_differing_dates": differ, "rows": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(Path(p) for p in glob.glob(str(Path(args.src) / "*.pdf")))
    if not pdfs:
        print(f"no PDFs under {args.src}", file=sys.stderr)
        return 1

    combined: list[dict] = []
    dues: list[dict] = []
    for pdf in pdfs:
        res = parse(pdf)
        rows = build_rows(res, pdf)
        st = selftest(res, rows)
        combined.extend([{**c, "source_file": pdf.name} for c in st["checks"]])

        with (out / f"amex-{pdf.stem}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

        payments = [r for r in rows if r["extras"]["is_card_payment"]]
        s = res["summary"] or {}
        manifest = {
            "source_file": pdf.name,
            "provider": "amex",
            "statement_period": {"from": res["period_from"], "to": res["period_to"]},
            "card_ref": mask_card(res["card_last"]) if res["card_last"] else None,
            "summary": {k: v for k, v in s.items() if k != "summary_line"},
            "tx_count": len(rows),
            "charges_minor": -sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] < 0),
            "credits_minor": sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] > 0),
            "card_payment_count": len(payments),
            "card_payment_minor": [r["raw_amount_minor"] for r in payments],
            "refund_count": sum(1 for r in rows
                                if r["extras"]["is_credit"] and not r["extras"]["is_card_payment"]),
            "rows_with_differing_dates": st["rows_with_differing_dates"],
            "foreign_currency_populated": False,
            "unparsed_count": len(res["unparsed"]),
            "total_lines": res["total_lines"],
        }
        (out / f"amex-{pdf.stem}_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"amex-{pdf.stem}_selftest.json").write_text(
            json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"amex-{pdf.stem}_parse_report.json").write_text(
            json.dumps({
                "source_file": pdf.name,
                "unparsed_regions": res["unparsed"],
                "unparsed_count": len(res["unparsed"]),
                "total_lines": res["total_lines"],
                "skipped_marker_lines": list(SKIP_MARKERS),
                "notes": [
                    "A credit is identified by a line containing only 'CR' beneath the "
                    "amount; the amount column itself carries no sign.",
                    "The credit section mixes the monthly card payment with genuine "
                    "refunds, and both appear as positive amounts, so the two are "
                    "separated by description rather than by sign.",
                    "The foreign-currency column is empty in all four statements, so no "
                    "original amount or exchange rate is available from this export.",
                    "Transaction date and processing date differ on roughly a third of "
                    "rows, so raw_posting_date is load-bearing and cannot be assumed "
                    "equal to raw_date.",
                ],
            }, indent=2, ensure_ascii=False), encoding="utf-8")

        dues.append({"source_file": pdf.name, "period_to": res["period_to"],
                     "amount_due_minor": s.get("amount_due_minor"),
                     "card_payment_minor": payments[0]["raw_amount_minor"] if payments else None})
        print(f"{pdf.name}: {len(rows)} rows, period {res['period_from']}..{res['period_to']}, "
              f"{st['rows_with_differing_dates']} rows with differing dates")
        for c in st["checks"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']}: {c['detail']}")

    ok = all(c["pass"] for c in combined)
    (out / "amex_selftest.json").write_text(
        json.dumps({"pass": ok, "checks": combined, "amount_due_vs_card_payment": dues},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  OVERALL: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
