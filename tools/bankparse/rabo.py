#!/usr/bin/env python3
"""Parse Rabobank monthly account-statement PDFs into normalized JSONL.

Format notes, verified against the four supplied statements (2026-06 .. 2026-09):

* The table is ``Value date | Type | Counterparty account | Name/description |
  Debit amount | Credit amount``, and the column header is repeated at the top of every
  page -- but the *character offsets* of the Debit and Credit columns differ from page
  to page (e.g. Debit@91/Credit@113 on page 1, Debit@86/Credit@102 on page 2,
  Debit@107/Credit@131 on page 5). An amount is therefore classified against the header
  of the page it appears on. A single global column offset silently mislabels every
  amount and still happens to balance, so the rule is checked against the statement's
  own stated totals in the self-test rather than assumed.
* Only one of Debit/Credit is populated per row, so a row carries a single amount.
* ``Value date`` is ``DD-MM`` with no year; the year comes from the statement period.
* A record is a header line plus zero or more more-indented continuation lines carrying
  SEPA sub-fields: ``Mandate Identifier / Creditor ID:``, ``End-to-End ID:``,
  ``Processing date:``, ``Invoice ...``, ``. Pas: ... Terminal: ...``, ``. Appr Cd: ...``.
* ``End-to-End ID`` is captured raw and is never treated as a key: observed values
  include a bare ``261912``, a value with an embedded datetime, and ``AI0001398/112``.
* The last page carries a type-code legend, which is parsed rather than hardcoded.

Sign convention: credit positive, debit negative. IBANs are reduced to their last four
characters before reaching disk, including inside free-text description fields.
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
ROW_RE = re.compile(r"^\s*(\d{2})-(\d{2})\s+([a-z]{2})(\s|$)")
HEADER_RE = re.compile(r"Debit amount\s+Credit amount")
LEGEND_RE = re.compile(r"(?:^|\s)([a-z]{2})\s*=\s")
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,4}[A-Z0-9]{1,4})\b")
# Dutch IBANs print as "NL79 RABO 0000 0000 00": country+check digit, then a 4-letter
# bank code, then three numeric groups. The bank code is what a compact-format regex
# misses, and missing it means the account is never captured and no redaction happens.
SPACED_IBAN_RE = re.compile(
    r"\b([A-Z]{2}\d{2}[ ][A-Z]{4}[ ]\d{4}[ ]\d{4}[ ]\d{2})\b")
# Dutch SEPA creditor identifier, e.g. NL00ZZZ000000000000.
CREDITOR_ID_RE = re.compile(r"^[A-Z]{2}\d{2}ZZZ\d{6,}$")
# Any printed IBAN: 2 letters + 2 check digits, optional 4-letter bank code, then 1-4
# numeric groups. Used only for redaction, so it is deliberately permissive.
ANY_SPACED_IBAN_RE = re.compile(
    r"\b([A-Z]{2}\d{2}(?:[ ][A-Z]{4})?(?:[ ]\d{2,4}){2,5})\b")
FIELD_RE = re.compile(r"([A-Za-z][A-Za-z /]*?):\s*(.+)$")
PROCESSING_RE = re.compile(r"Processing date:\s*(\d{2}-\d{2}-\d{4})", re.I)
TERMINAL_RE = re.compile(r"Terminal:\s*(\d+)")
PAS_RE = re.compile(r"Pas:\s*\d*[xX*]*(\d{4})")
APPROVAL_RE = re.compile(r"Appr\s*C?d:\s*([0-9A-Fa-f]+)", re.I)
KENMERK_RE = re.compile(r"Kenmerk\s*-?\s*(\S+)", re.I)
INVOICE_RE = re.compile(r"Invoice\s+(\S+)", re.I)

EXTRACT_KEYS = (
    "mandate_id", "creditor_id", "end_to_end_id", "processing_date", "terminal",
    "card_last4", "approval_code", "invoice_ref", "kenmerk",
)


def to_minor(text: str) -> int:
    """'1.260,00' -> 126000. Dot is thousands, comma is decimal."""
    s = text.replace(".", "").replace(",", "")
    if not s.isdigit():
        raise ValueError(f"unparseable Dutch amount: {text!r}")
    return int(s)


def fingerprint(account_ref: str, date: str, amount_minor: int, currency: str, desc: str) -> str:
    norm = re.sub(r"\s+", " ", desc).strip().upper()
    return hashlib.sha256(
        f"rabobank|{account_ref}|{date}|{amount_minor}|{currency}|{norm}".encode()
    ).hexdigest()


def mask_iban(iban: str) -> str:
    return "..." + re.sub(r"\s+", "", iban)[-4:]


def redact(text: str | None) -> str | None:
    """Mask any IBAN embedded in free text before it reaches disk.

    The layout is not uniform across countries: Dutch IBANs print three numeric groups
    after a 4-letter bank code, Irish ones print four. A regex pinned to the Dutch shape
    silently passes foreign counterparty IBANs through, so match any run of 1-4 numeric
    groups and keep the last 4 characters of whatever matched.
    """
    if text is None:
        return None
    out = ANY_SPACED_IBAN_RE.sub(lambda m: mask_iban(m.group(1)), text)
    return IBAN_RE.sub(lambda m: mask_iban(m.group(1)), out)


def extract_text(pdf: Path) -> str:
    r = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                       capture_output=True, text=True, check=True)
    return r.stdout


def _field_after(lines: list[str], label: str) -> str | None:
    """Value that sits on the line below a two-column header label."""
    for i, line in enumerate(lines):
        if label in line and i + 1 < len(lines):
            nxt = lines[i + 1]
            m = re.search(r"([\d.,]+)\s*(CR|DR)?\s*$", nxt)
            if m and re.search(r"\d", nxt):
                return m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
    return None


def _field_on_next_line(lines: list[str], label: str) -> str | None:
    """Bare value on the following line, e.g. From (date) -> 01-09-2026.

    The label is matched anywhere in the line, not just at its start: "From (date)"
    shares its line with the postal address, while "To (date)" starts its own line.
    """
    for i, line in enumerate(lines):
        if label in line and i + 1 < len(lines):
            nxt = lines[i + 1]
            m = re.search(r"\d{2}-\d{2}-\d{4}", nxt)
            if m:
                return m.group(0)
    return None


def parse_header(lines: list[str]) -> dict:
    def money(tok: str | None) -> int | None:
        if not tok:
            return None
        return to_minor(tok.replace(" CR", "").replace(" DR", ""))

    prev_raw = _field_after(lines, "Previous balance")
    close_raw = _field_after(lines, "Closing balance")

    totals = {}
    for i, line in enumerate(lines):
        if "Total amount debited" in line and i + 1 < len(lines):
            nums = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", lines[i + 1])
            if len(nums) >= 2:
                totals["total_debited_minor"] = to_minor(nums[0])
                totals["total_credited_minor"] = to_minor(nums[1])
        if "IBAN / account number" in line and i + 1 < len(lines):
            nxt = lines[i + 1]
            m = SPACED_IBAN_RE.search(nxt)
            if m:
                totals["account_iban"] = re.sub(r"\s+", "", m.group(1))
            b = re.search(r"\b([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", nxt)
            if b:
                totals["bic"] = b.group(1)
        if "Statement creation date" in line and i + 1 < len(lines):
            d = re.search(r"\d{2}-\d{2}-\d{4}", lines[i + 1])
            if d:
                totals["statement_creation_date"] = d.group(0)
        m = re.search(r"\b(\d+)\s+of\s+(\d+)\b", line)
        if m and "Page" in line:
            totals["pages"] = int(m.group(2))

    per = _field_on_next_line(lines, "From (date)")
    to = _field_on_next_line(lines, "To (date)")
    return {
        "period_from": per,
        "period_to": to,
        "previous_balance_minor": money(prev_raw),
        "previous_balance_raw": prev_raw,
        "closing_balance_minor": money(close_raw),
        "closing_balance_raw": close_raw,
        **totals,
    }


def year_for(month: int, period_from: str | None, period_to: str | None) -> int:
    """Value dates carry no year; infer it from the statement period."""
    y_to = int(period_to.split("-")[-1]) if period_to else 2026
    y_from = int(period_from.split("-")[-1]) if period_from else y_to
    if y_from != y_to and month == 12:
        return y_from
    return y_to


def parse_legend(lines: list[str]) -> dict[str, str]:
    legend: dict[str, str] = {}
    for line in lines:
        hits = list(LEGEND_RE.finditer(line))
        if len(hits) < 2:
            continue
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(line)
            meaning = line[m.end():end].strip()
            if meaning:
                legend.setdefault(m.group(1), meaning)
    return legend


def parse(pdf: Path) -> dict:
    lines = extract_text(pdf).split("\n")

    headers: list[tuple[int, int, int]] = []
    for i, line in enumerate(lines, 1):
        m = HEADER_RE.search(line)
        if m:
            headers.append((i, m.start(), m.end()))

    head = parse_header(lines)
    legend = parse_legend(lines)

    period_from = head.get("period_from")
    rows: list[dict] = []
    unparsed: list[dict] = []
    cur: tuple[int, int, int] | None = None
    last: dict | None = None
    pending_key: str | None = None

    for i, line in enumerate(lines, 1):
        for h in headers:
            if h[0] <= i:
                cur = h
        m = HEADER_RE.search(line)
        if m:
            last, pending_key = None, None
            continue
        if not line.strip():
            continue

        rm = ROW_RE.match(line)
        if rm:
            day, mon, code, _ = rm.groups()
            if not cur:
                unparsed.append({"line_no": i, "text": line.strip()[:120],
                                 "reason": "row before any column header"})
                continue
            _, debit_at, credit_at = cur
            boundary = (debit_at + credit_at) // 2
            amts = [(mm.start(), mm.group(1)) for mm in AMOUNT_RE.finditer(line)]
            amount_raw = amts[-1][1] if amts else None
            is_credit = bool(amts) and amts[-1][0] >= boundary
            body = line[rm.end():]
            if amount_raw:
                idx = line.rfind(amount_raw)
                body = line[rm.end():idx]
            desc = re.sub(r"\s{2,}", " ", body).strip()
            cp = SPACED_IBAN_RE.match(desc)
            counterparty = mask_iban(cp.group(1)) if cp else None
            month = int(mon)
            date = f"{year_for(month, period_from, head.get('period_to')):04d}-{month:02d}-{int(day):02d}"
            amount = to_minor(amount_raw) if amount_raw else None
            signed = None if amount is None else (amount if is_credit else -amount)
            last = {
                "value_date": date, "type_code": code, "description": desc,
                "counterparty_iban_masked": counterparty, "amount_raw": amount_raw,
                "is_credit": is_credit, "amount_minor": amount, "signed_minor": signed,
                "line_no": i, "extras": {},
            }
            rows.append(last)
            pending_key = None
            continue

        if last is None:
            continue

        stripped = line.strip()
        pr = PROCESSING_RE.search(stripped)
        if pr:
            last["extras"]["processing_date"] = pr.group(1)
            pending_key = None
            continue
        if TERMINAL_RE.search(stripped):
            last["extras"]["terminal"] = TERMINAL_RE.search(stripped).group(1)
            pending_key = None
            continue
        if PAS_RE.search(stripped):
            last["extras"]["card_last4"] = PAS_RE.search(stripped).group(1)
            pending_key = None
            continue
        if APPROVAL_RE.search(stripped):
            last["extras"]["approval_code"] = APPROVAL_RE.search(stripped).group(1)
            pending_key = None
            continue
        if INVOICE_RE.search(stripped):
            last["extras"]["invoice_ref"] = INVOICE_RE.search(stripped).group(1)
            pending_key = None
            continue
        if KENMERK_RE.search(stripped):
            last["extras"]["kenmerk"] = KENMERK_RE.search(stripped).group(1)
            pending_key = None
            continue

        label = re.match(r"^([A-Za-z][A-Za-z /-]*?):\s*$", stripped)
        if label:
            key = label.group(1).strip().lower().replace(" ", "_").replace("/", "_and_")
            if "mandate" in key or "creditor" in key:
                pending_key = "mandate_id"
            elif "end" in key and "to" in key:
                pending_key = "end_to_end_id"
            else:
                pending_key = key if key in EXTRACT_KEYS else None
            continue

        if pending_key and stripped:
            cur_val = last["extras"].get(pending_key, "")
            last["extras"][pending_key] = (cur_val + " " + stripped).strip()
            continue

        if stripped and line.startswith(" "):
            last["extras"].setdefault("supplementary", "")
            last["extras"]["supplementary"] = (
                last["extras"]["supplementary"] + " " + stripped).strip()
            continue

        unparsed.append({"line_no": i, "text": redact(stripped[:120]),
                         "reason": "unrecognised non-row line"})

    for r in rows:
        r["extras"] = {k: redact(v) for k, v in r["extras"].items() if v not in (None, "")}

    return {"header": head, "legend": legend, "rows": rows,
            "unparsed": unparsed, "header_repeats": len(headers),
            "total_lines": len(lines)}


def build_rows(res: dict, pdf: Path) -> list[dict]:
    ref = mask_iban(res["header"]["account_iban"]) if res["header"].get("account_iban") else None
    out = []
    for r in res["rows"]:
        if r["signed_minor"] is None:
            continue
        extras = dict(r["extras"])
        extras["type_code"] = r["type_code"]
        extras["type_meaning"] = res["legend"].get(r["type_code"])
        extras["is_credit"] = r["is_credit"]
        extras["counterparty_iban_masked"] = r["counterparty_iban_masked"]
        out.append({
            "source_file": pdf.name,
            "provider": "rabobank",
            "account_ref": ref,
            "raw_date": r["value_date"],
            "raw_posting_date": r["extras"].get("processing_date"),
            "raw_description": redact(r["description"]),
            "raw_amount_minor": r["signed_minor"],
            "raw_currency": "EUR",
            "provider_txn_id": None,
            "fingerprint": fingerprint(ref, r["value_date"], r["signed_minor"], "EUR", r["description"]),
            "extras": extras,
        })
    return out


def selftest(res: dict, rows: list[dict], pdf: Path) -> dict:
    h = res["header"]
    checks: list[dict] = []

    def add(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    credits = sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] > 0)
    debits = -sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] < 0)
    sd, sc = h.get("total_debited_minor"), h.get("total_credited_minor")
    if sd is not None:
        add("extracted debits == stated Total amount debited",
            debits == sd, f"extracted={debits} stated={sd} delta={debits - sd}")
    if sc is not None:
        add("extracted credits == stated Total amount credited",
            credits == sc, f"extracted={credits} stated={sc} delta={credits - sc}")
    pb, cb = h.get("previous_balance_minor"), h.get("closing_balance_minor")
    if None not in (pb, cb):
        add("previous + credits - debits == closing balance",
            pb + credits - debits == cb,
            f"{pb} + {credits} - {debits} = {pb + credits - debits} vs closing {cb}")
    return {"pass": all(c["pass"] for c in checks), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(Path(p) for p in glob.glob(str(Path(args.src) / "*"))
                  if not p.lower().endswith(".json"))
    pdfs = [p for p in pdfs if p.is_file()]
    if not pdfs:
        print(f"no files under {args.src}", file=sys.stderr)
        return 1

    all_checks: list[dict] = []
    seen_codes: set[str] = set()
    legend: dict[str, str] = {}
    balances: list[tuple[str, int | None, int | None]] = []

    for pdf in pdfs:
        res = parse(pdf)
        rows = build_rows(res, pdf)
        st = selftest(res, rows, pdf)
        all_checks.extend([{**c, "source_file": pdf.name} for c in st["checks"]])

        with (out / f"rabo-{pdf.stem}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

        seen_codes |= {r["extras"]["type_code"] for r in rows}
        legend.update(res["legend"])
        h = res["header"]
        balances.append((h.get("period_from") or pdf.name, h.get("previous_balance_minor"),
                         h.get("closing_balance_minor")))

        manifest = {
            "source_file": pdf.name,
            "provider": "rabobank",
            **{k: v for k, v in h.items() if "iban" not in k.lower()},
            "account_ref": (mask_iban(h["account_iban"]) if h.get("account_iban") else None),
            "column_header_repeats": res["header_repeats"],
            "tx_count": len(rows),
            "credits_minor": sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] > 0),
            "debits_minor": -sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] < 0),
            "type_codes_seen": sorted({r["extras"]["type_code"] for r in rows}),
        }
        (out / f"rabo-{pdf.stem}_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"rabo-{pdf.stem}_selftest.json").write_text(
            json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"rabo-{pdf.stem}_parse_report.json").write_text(
            json.dumps({
                "source_file": pdf.name,
                "unparsed_regions": res["unparsed"],
                "unparsed_count": len(res["unparsed"]),
                "total_lines": res["total_lines"],
                "type_code_legend": res["legend"],
                "type_codes_seen": sorted({r["extras"]["type_code"] for r in rows}),
                "type_codes_undefined": sorted(
                    {r["extras"]["type_code"] for r in rows} - set(res["legend"])),
                "notes": [
                    "Debit vs credit is resolved per page from that page's own column "
                    "header, whose offsets differ between pages; the stated totals and the "
                    "balance identity in the self-test are what validate the rule.",
                    "End-to-End ID is captured raw and never used as a key: observed values "
                    "include a bare number, a value with an embedded datetime, and an "
                    "AI-prefixed code.",
                    "Column headers repeat once per page, and the legend block is parsed from "
                    "the last page rather than hardcoded.",
                ],
            }, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"{pdf.name}: {len(rows)} rows, period {h.get('period_from')}..{h.get('period_to')}")
        for c in st["checks"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']}: {c['detail']}")

    joins: list[dict] = []
    for a, b in zip(balances, balances[1:]):
        ok = a[2] is not None and b[1] is not None and a[2] == b[1]
        joins.append({"from_period": a[0], "to_period": b[0],
                      "earlier_closing": a[2], "later_previous": b[1], "pass": ok})
    (out / "rabo_continuity.json").write_text(
        json.dumps({"joins": joins,
                    "pass": all(j["pass"] for j in joins) if joins else None}, indent=2),
        encoding="utf-8")

    undefined = sorted(seen_codes - set(legend))
    combined = {"pass": all(c["pass"] for c in all_checks) and
                          (all(j["pass"] for j in joins) if joins else True),
                "per_file_checks": all_checks, "cross_file_balance_joins": joins,
                "type_codes_seen": sorted(seen_codes), "type_code_legend": legend,
                "type_codes_seen_but_undefined": undefined}
    (out / "rabo_selftest.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n-- cross-file balance joins --")
    for j in joins:
        print(f"  [{'PASS' if j['pass'] else 'FAIL'}] {j['from_period']} closing "
              f"{j['earlier_closing']} -> {j['to_period']} previous {j['later_previous']}")
    print(f"  type codes seen: {', '.join(sorted(seen_codes))}")
    if undefined:
        print(f"  codes seen but NOT in the printed legend: {', '.join(undefined)}")
    print(f"  OVERALL: {'PASS' if combined['pass'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
