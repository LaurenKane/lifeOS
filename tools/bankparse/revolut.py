#!/usr/bin/env python3
"""Parse a Revolut Bank annual statement PDF into normalized JSONL.

Format notes, verified against
Rev/account-statement_2026-01-01_2026-10-01_en-gb_783080.pdf:

* One file can cover several products. The observed statement carries an
  "Account transactions ..." section and a "Deposit transactions ..." section, and the
  balance summary has one row per product. Nothing in the design docs covers this.
* Amounts use a PERIOD decimal separator: "EUR 5,378.27" -> 537827 minor units, where the
  comma is a thousands separator. This is the opposite convention from the Dutch
  statements, where "5.471,14" -> 547114. Never reuse one converter for both.
* Money out and money in are separate columns rather than a single signed column.
* A description may wrap onto following, more-indented lines. Those carry either
  sub-fields ("To: <counterparty>", "Card: 416598******3958") or the tail of a wrapped
  description ("Net Interest Paid ... for 25 May" then "2026").
* The final column is a running balance, so per-row arithmetic is checkable. That check
  is what proves this parse is complete rather than merely plausible.

Sign convention: money leaving the account is negative, money arriving is positive.
IBANs are reduced to their last 4 characters before being written to any output file.
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

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Balances go negative, and the statement prints the sign as a leading "-" outside the
# symbol ("-EUR 9.10"), so the sign has to be matched before the currency, not inside
# the digits. "$" amounts appear only on continuation lines as FX context, never in
# the EUR columns.
AMOUNT_RE = re.compile(r"(-?)€\s*([\d.,]+)")
ROW_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Z][a-z]{2,4})\s+(\d{4})\s{2,}")
SECTION_RE = re.compile(r"^\s*(\w+)\s+transactions\s+from\s+(.+?)\s+to\s+(.+?)\s*$", re.I)
COLHDR_RE = re.compile(r"Description.*Money\s+out.*Money\s+in.*Balance", re.I)
SUMHDR_RE = re.compile(r"Product.*Opening\s+balance.*Money\s+out.*Money\s+in", re.I)
CLOSING_LABEL_RE = re.compile(r"Closing")
BALANCE_LABEL_RE = re.compile(r"\bBalance\b")
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")
BIC_RE = re.compile(r"\bBIC\s+([A-Z0-9]{8,11})\b")
GENERATED_RE = re.compile(r"Generated on the (.+?)\s*$", re.I | re.M)
CARD_RE = re.compile(r"Card:\s*(\d{6})\*+(\d{4})")
TO_RE = re.compile(r"^To:\s*(.+)$")
FROM_RE = re.compile(r"^From:\s*(.+)$")
REFERENCE_RE = re.compile(r"^Reference:\s*(.+)$")
FX_AMOUNT_RE = re.compile(r"^\$([\d.,]+)$")

FOOTER_MARKERS = (
    "Report lost or stolen card",
    "Get help directly in app",
    "Scan the QR code",
    "© 2026 Revolut",
    "AFM number",
)


def to_minor(text: str) -> int:
    """'€5,378.27' -> 537827. Period is decimal, comma is thousands."""
    s = text.replace("€", "").strip().replace(" ", "")
    if not s:
        raise ValueError("empty amount")
    neg = s.startswith("-")
    s = s.lstrip("-").replace(",", "")
    if "." in s:
        whole, _, frac = s.partition(".")
        frac = (frac + "00")[:2]
    else:
        whole, frac = s, "00"
    if not whole.isdigit() or not frac.isdigit():
        raise ValueError(f"unparseable amount: {text!r}")
    val = int(whole) * 100 + int(frac)
    return -val if neg else val


def fingerprint(account_ref: str, date: str, amount_minor: int, currency: str, desc: str) -> str:
    norm = re.sub(r"\s+", " ", desc).strip().upper()
    key = f"revolut|{account_ref}|{date}|{amount_minor}|{currency}|{norm}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def mask_iban(iban: str) -> str:
    return "..." + iban[-4:]


def redact(text: str | None) -> str | None:
    """Mask any IBAN inside a free-text field before it reaches disk.

    Counterparty and reference strings carry full IBANs (12 distinct ones appear in
    this statement). The repo's never-log rule (R06, ARCH:726) says log last 4 only, and
    these outputs are the durable copy, so masking has to happen at parse time rather
    than at display time.
    """
    if text is None:
        return None
    return IBAN_RE.sub(lambda m: mask_iban(m.group(1)), text)


def col_anchors(*header_lines: str) -> dict[str, int]:
    """Midpoint boundaries between left-aligned column labels, in character offsets.

    Takes the header as a block rather than one line because the summary table wraps
    "Closing balance" over two lines. Labels are left-aligned, so the first offset at
    which any block line contains a label is that column's start.
    """
    idx: dict[str, int] = {}
    for name, phrase in (("open", "Opening balance"), ("out", "Money out"),
                         ("in", "Money in"), ("close", "Closing balance")):
        positions = [ln.index(phrase) for ln in header_lines if phrase in ln]
        if positions:
            idx[name] = min(positions)
    if "close" not in idx:
        # The transaction table labels its last column plain "Balance"; the summary table
        # splits "Closing balance" over two lines. Without a close anchor the running
        # balance would be misclassified into the "Money in" column.
        cands = []
        for ln in header_lines:
            for pat in (CLOSING_LABEL_RE, BALANCE_LABEL_RE):
                m = pat.search(ln)
                if m:
                    cands.append(m.start())
        if cands:
            idx["close"] = min(cands)
    if not {"out", "in", "close"} <= idx.keys():
        return {}
    seq = sorted(idx.items(), key=lambda kv: kv[1])
    bounds = {}
    for i, (name, start) in enumerate(seq):
        end = seq[i + 1][1] if i + 1 < len(seq) else 10**6
        bounds[name] = (start + end) // 2
    return bounds


def classify(tokens: list[tuple[int, int]], bounds: dict[str, int], order: list[str]) -> dict[str, int]:
    """Assign each (offset, minor) amount to a column by nearest preceding boundary."""
    out: dict[str, int] = {}
    for pos, val in tokens:
        chosen = order[-1]
        for name in order:
            if pos < bounds.get(name, 10**6):
                chosen = name
                break
        out[chosen] = val
    return out


def parse_amounts(line: str) -> list[tuple[int, int]]:
    res = []
    for m in AMOUNT_RE.finditer(line):
        sign, digits = m.group(1), m.group(2)
        try:
            val = to_minor(digits)
        except ValueError:
            continue
        res.append((m.start(), -val if sign and val > 0 else val))
    return res


def parse_date(d: str, mon: str, yr: str) -> str:
    return f"{yr}-{MONTHS[mon]:02d}-{int(d):02d}"


def extract_text(pdf: Path) -> str:
    r = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                       capture_output=True, text=True, check=True)
    return r.stdout


def parse_summary(lines: list[str], start: int) -> tuple[list[dict], int]:
    """Parse the balance summary table. Returns (rows, index_of_line_after_table)."""
    products, i, bounds = [], start, {}
    while i < len(lines):
        line = lines[i]
        if not bounds:
            if SUMHDR_RE.search(line):
                bounds = col_anchors(*lines[max(0, i - 2): i + 3])
            i += 1
            continue
        toks = parse_amounts(line)
        if len(toks) >= 4:
            # The product name ends where the first amount begins; the column boundary
            # midpoint is too far right and would swallow the opening balance.
            name = re.sub(r"\s{2,}", " ", line[: toks[0][0]]).strip()
            vals = classify(toks, bounds, ["open", "out", "in", "close"])
            if name:
                products.append({
                    "product": name,
                    "opening_minor": vals.get("open"),
                    "money_out_minor": vals.get("out"),
                    "money_in_minor": vals.get("in"),
                    "closing_minor": vals.get("close"),
                })
            i += 1
            continue
        if line.strip() and not toks:
            # The wrapped "balance" line of the header, and blank spacer lines, are not
            # table terminators. Terminate only on a line that starts the next section
            # or on running out of rows that look like products.
            if SECTION_RE.match(line) or (products and re.match(
                    r"^\s*[\w()]+\s{2,}", line) and not CLOSING_LABEL_RE.search(line)
                    and "balance" not in line.lower()):
                break
            i += 1
            continue
        i += 1
    return products, i


PAGE_NUM_RE = re.compile(r"Page\s+\d+\s+of\s+\d+")
REPEAT_HEADER_RE = re.compile(
    r"^\s*(EUR Statement|Generated on the|Revolut Bank UAB \(Netherlands Branch\))\s*$")


def strip_page_furniture(lines: list[str]) -> tuple[list[str], int, int]:
    """Drop the per-page legal/footer block and the repeated page header.

    The statement is 18 pages and repeats the same footer and running header on every
    one, so a single truncation at the first footer marker would silently discard the
    other 17 pages. Each footer runs from its first line to the "Page N of M" line.
    """
    kept: list[str] = []
    in_footer = False
    dropped = 0
    first_footer_line = None
    for i, line in enumerate(lines):
        if not in_footer and any(m in line for m in FOOTER_MARKERS):
            in_footer = True
            if first_footer_line is None:
                first_footer_line = i + 1
        if in_footer:
            dropped += 1
            if PAGE_NUM_RE.search(line):
                in_footer = False
            continue
        if REPEAT_HEADER_RE.match(line):
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped, (first_footer_line or 0)


def parse(pdf: Path) -> dict:
    text = extract_text(pdf)
    raw_lines = text.split("\n")
    body, dropped_lines, first_footer_line = strip_page_furniture(raw_lines)
    lines = body

    gen = GENERATED_RE.search(text)
    generated = gen.group(1).strip() if gen else None

    ibans, seen = [], set()
    for m in IBAN_RE.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            ibans.append(m.group(1))
    bics = sorted({m.group(1) for m in BIC_RE.finditer(text)})

    primary = ibans[0] if ibans else None
    secondary = ibans[1] if len(ibans) > 1 else None

    summary: list[dict] = []
    for idx, line in enumerate(body):
        if line.strip() == "Balance summary":
            summary, _ = parse_summary(body, idx + 1)
            break

    sum_by_product = {p["product"]: p for p in summary}

    sections: list[dict] = []
    cur: dict | None = None
    bounds: dict[str, int] = {}
    i = 0
    unparsed: list[dict] = []
    last_row: dict | None = None

    while i < len(body):
        line = body[i]
        m = SECTION_RE.match(line)
        if m:
            cur = {"name": m.group(1), "from": m.group(2), "to": m.group(3), "rows": []}
            sections.append(cur)
            bounds = {}
            last_row = None
            i += 1
            continue

        if cur is None:
            i += 1
            continue

        if COLHDR_RE.search(line):
            # The column header repeats at the top of every page, so it can reappear
            # after rows have already been read. Re-anchor each time rather than only
            # on the first occurrence.
            bounds = col_anchors(line)
            i += 1
            continue

        if not bounds:
            if not line.strip():
                i += 1
                continue
            unparsed.append({"line_no": i + 1, "text": line.strip()[:120],
                             "reason": "content before any column header in this section"})
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        rm = ROW_RE.match(line)
        if rm:
            date = parse_date(*rm.groups())
            toks = parse_amounts(line)
            desc_raw = line[rm.end():]
            vals = classify(toks, bounds, ["out", "in", "close"])
            desc = re.sub(r"\s{2,}.*$", "", desc_raw).strip()
            has_balance = "close" in vals
            if not desc and not toks:
                unparsed.append({"line_no": i + 1, "text": line.strip()[:120],
                                 "reason": "row anchor matched but no description and no amount"})
            last_row = {
                "date": date,
                "description": desc,
                "money_out_minor": vals.get("out"),
                "money_in_minor": vals.get("in"),
                "running_balance_minor": vals.get("close") if has_balance else None,
                "extras": {},
                "line_no": i + 1,
            }
            cur["rows"].append(last_row)
            i += 1
            continue

        if last_row is not None:
            cm = CARD_RE.search(line)
            tm = TO_RE.match(line.strip())
            if cm:
                last_row["extras"]["card_last4"] = cm.group(2)
                last_row["extras"]["card_bin6"] = cm.group(1)
                i += 1
                continue
            if tm:
                last_row["extras"]["counterparty"] = redact(tm.group(1).strip())
                i += 1
                continue
            fm = FROM_RE.match(line.strip())
            if fm:
                last_row["extras"]["counterparty_from"] = redact(fm.group(1).strip())
                i += 1
                continue
            rm2 = REFERENCE_RE.match(line.strip())
            if rm2:
                last_row["extras"]["reference"] = redact(rm2.group(1).strip())
                i += 1
                continue
            fx = FX_AMOUNT_RE.match(line.strip())
            if fx:
                # Foreign-currency context for a EUR row (investment top-ups). Recorded
                # raw; there is no exchange rate in the statement to pair it with.
                last_row["extras"]["foreign_amount_raw"] = fx.group(1)
                i += 1
                continue
            if line.startswith(" ") and line.strip():
                last_row["description"] = (last_row["description"] + " " + line.strip()).strip()
                i += 1
                continue
            unparsed.append({"line_no": i + 1, "text": line.strip()[:120],
                             "reason": "indented non-row line not recognised as a known sub-field"})
            i += 1
            continue

        unparsed.append({"line_no": i + 1, "text": line.strip()[:120],
                         "reason": "content before the first row in this section"})
        i += 1

    return {
        "generated": generated,
        "ibans": ibans,
        "bics": bics,
        "primary_iban": primary,
        "secondary_iban": secondary,
        "summary": summary,
        "sections": sections,
        "unparsed": unparsed,
        "first_footer_line": first_footer_line,
        "page_furniture_lines_dropped": dropped_lines,
        "total_lines": len(raw_lines),
    }


def build_rows(res: dict, pdf: Path) -> tuple[list[dict], list[dict]]:
    """Map parsed sections to output rows, attributing each section to an account."""
    rows: list[dict] = []
    attribution: list[dict] = []
    for sec in res["sections"]:
        if sec["name"].lower() == "account":
            acct, inferred, note = (
                (res["primary_iban"], False, "primary NL IBAN for the Account section")
                if res["primary_iban"] else
                (None, True, "no IBAN found; account_ref unavailable")
            )
            product = "Current Account"
        elif sec["name"].lower() == "deposit":
            acct, inferred, note = (
                (res["secondary_iban"], True,
                 "Deposit product attributed to the secondary (LT) IBAN by inference; "
                 "the statement does not state this mapping explicitly")
                if res["secondary_iban"] else
                (None, True, "no secondary IBAN found; account_ref unavailable")
            )
            product = "Deposit"
        else:
            acct, inferred, note = None, True, f"unknown section name {sec['name']!r}"
            product = sec["name"]
        ref = mask_iban(acct) if acct else None
        attribution.append({"section": sec["name"], "product": product,
                            "account_ref": ref, "inferred": inferred, "note": note})
        for r in sec["rows"]:
            out_minor = r["money_out_minor"]
            in_minor = r["money_in_minor"]
            if out_minor is None and in_minor is None:
                continue
            signed = -(out_minor or 0) + (in_minor or 0)
            extras = dict(r["extras"])
            extras["product"] = product
            extras["is_internal_transfer"] = bool(
                re.search(r"^(to|from)\s+savings$", r["description"], re.I)
            ) or bool(re.search(r"(to|from) instant access savings", r["description"], re.I))
            extras["running_balance_minor"] = r["running_balance_minor"]
            if inferred:
                extras["account_ref_inferred"] = True
            rows.append({
                "source_file": pdf.name,
                "provider": "revolut",
                "account_ref": ref,
                "raw_date": r["date"],
                "raw_posting_date": None,
                "raw_description": r["description"],
                "raw_amount_minor": signed,
                "raw_currency": "EUR",
                "provider_txn_id": None,
                "fingerprint": fingerprint(ref, r["date"], signed, "EUR", r["description"]),
                "extras": extras,
            })
    return rows, attribution


def selftest(res: dict, rows: list[dict], sections: list[dict]) -> dict:
    checks: list[dict] = []

    def add(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    summary = {p["product"]: p for p in res["summary"]}
    for sec in sections:
        product = "Current Account" if sec["name"].lower() == "account" else "Deposit"
        srow = summary.get(product)
        if not srow:
            continue
        srows = [r for r in rows if r["extras"]["product"] == product]
        got_out = -sum(r["raw_amount_minor"] for r in srows if r["raw_amount_minor"] < 0)
        got_in = sum(r["raw_amount_minor"] for r in srows if r["raw_amount_minor"] > 0)
        d_out = got_out - (srow["money_out_minor"] or 0)
        d_in = got_in - (srow["money_in_minor"] or 0)
        add(f"{product}: extracted money out == summary Money out",
            d_out == 0, f"extracted={got_out} summary={srow['money_out_minor']} delta={d_out}")
        add(f"{product}: extracted money in == summary Money in",
            d_in == 0, f"extracted={got_in} summary={srow['money_in_minor']} delta={d_in}")
        op, cl = srow["opening_minor"] or 0, srow["closing_minor"] or 0
        add(f"{product}: opening - out + in == closing",
            op - (srow["money_out_minor"] or 0) + (srow["money_in_minor"] or 0) == cl,
            f"{op} - {(srow['money_out_minor'] or 0)} + {(srow['money_in_minor'] or 0)} = "
            f"{op - (srow['money_out_minor'] or 0) + (srow['money_in_minor'] or 0)} vs closing {cl}")

    total = summary.get("Total")
    all_rows = rows
    got_out = -sum(r["raw_amount_minor"] for r in all_rows if r["raw_amount_minor"] < 0)
    got_in = sum(r["raw_amount_minor"] for r in all_rows if r["raw_amount_minor"] > 0)
    if total:
        d_out = got_out - (total["money_out_minor"] or 0)
        d_in = got_in - (total["money_in_minor"] or 0)
        add("Total: extracted money out == summary Total Money out",
            d_out == 0, f"extracted={got_out} summary={total['money_out_minor']} delta={d_out}")
        add("Total: extracted money in == summary Total Money in",
            d_in == 0, f"extracted={got_in} summary={total['money_in_minor']} delta={d_in}")

    for sec in sections:
        product = "Current Account" if sec["name"].lower() == "account" else "Deposit"
        checked = broken = 0
        offenders = []
        prev = None
        for r in [x for x in rows if x["extras"]["product"] == product]:
            bal = r["extras"].get("running_balance_minor")
            if bal is None:
                continue
            if prev is not None:
                checked += 1
                if prev + r["raw_amount_minor"] != bal:
                    broken += 1
                    if len(offenders) < 5:
                        offenders.append({
                            "date": r["raw_date"], "description": r["raw_description"],
                            "expected": prev + r["raw_amount_minor"], "found": bal,
                        })
            prev = bal
        first = next((x for x in rows if x["extras"]["product"] == product), None)
        srow = summary.get(product)
        if first and srow and srow["opening_minor"] is not None:
            fb = first["extras"].get("running_balance_minor")
            ok = fb == srow["opening_minor"] + first["raw_amount_minor"]
            add(f"{product}: first running balance consistent with summary opening",
                ok, f"opening={(srow['opening_minor'])} first_row_amount={first['raw_amount_minor']} "
                    f"first_balance={fb}")
        add(f"{product}: running-balance continuity across all rows",
            broken == 0,
            f"rows_checked={checked} rows_broken={broken} offenders={offenders}")

    return {"pass": all(c["pass"] for c in checks), "checks": checks}


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

    combined_checks: list[dict] = []
    for pdf in pdfs:
        res = parse(pdf)
        rows, attribution = build_rows(res, pdf)
        st = selftest(res, rows, res["sections"])
        combined_checks.extend(st["checks"])

        with (out / f"revolut-{pdf.stem}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

        counts: dict[str, int] = {}
        for r in rows:
            counts[r["extras"]["product"]] = counts.get(r["extras"]["product"], 0) + 1
        manifest = {
            "source_file": pdf.name,
            "provider": "revolut",
            "statement_generated": res["generated"],
            "sections": [{"name": s["name"], "from": s["from"], "to": s["to"],
                          "row_count": len(s["rows"])} for s in res["sections"]],
            "ibans_masked": [mask_iban(i) for i in res["ibans"]],
            "bics": res["bics"],
            "summary_table": res["summary"],
            "product_attribution": attribution,
            "tx_count": len(rows),
            "tx_count_by_product": counts,
            "money_out_minor": -sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] < 0),
            "money_in_minor": sum(r["raw_amount_minor"] for r in rows if r["raw_amount_minor"] > 0),
            "internal_transfer_count": sum(1 for r in rows if r["extras"]["is_internal_transfer"]),
            "distinct_descriptions": len({r["raw_description"] for r in rows}),
            "first_footer_line": res["first_footer_line"],
            "page_furniture_lines_dropped": res["page_furniture_lines_dropped"],
            "total_lines": res["total_lines"],
        }
        (out / f"revolut-{pdf.stem}_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        section_desc_counts: dict[str, int] = {}
        for r in rows:
            key = re.sub(r"\s+", " ", r["raw_description"]).strip().upper()
            section_desc_counts[key] = section_desc_counts.get(key, 0) + 1
        report = {
            "source_file": pdf.name,
            "unparsed_regions": res["unparsed"],
            "unparsed_count": len(res["unparsed"]),
            "first_footer_line": res["first_footer_line"],
            "page_furniture_lines_dropped": res["page_furniture_lines_dropped"],
            "total_lines_in_text": res["total_lines"],
            "internal_transfers": {
                "count": manifest["internal_transfer_count"],
                "note": "To/From Savings legs live in the Account section while the counter-leg "
                        "sits in the Deposit section, so they cannot be paired within a single "
                        "product and are inherently ambiguous to match on amount and date.",
            },
            "account_ref_inference": [
                a for a in attribution if a["inferred"]
            ],
            "card_last4_seen": sorted({r["extras"]["card_last4"] for r in rows
                                       if r["extras"].get("card_last4")}),
            "top_repeated_descriptions": sorted(
                section_desc_counts.items(), key=lambda kv: -kv[1])[:10],
            "warnings": [
                "One file covers 2 products and lists 2 IBANs; the Deposit->LT IBAN mapping is "
                "an inference, not something the statement states.",
                "Rows whose running balance is absent were excluded from the continuity check; "
                "their count is the difference between tx_count and rows_checked.",
            ],
        }
        (out / f"revolut-{pdf.stem}_parse_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"revolut-{pdf.stem}_selftest.json").write_text(
            json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"{pdf.name}: {len(rows)} rows -> {out / f'revolut-{pdf.stem}.jsonl'}")
        for c in st["checks"]:
            print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']}: {c['detail']}")

    (out / "revolut_selftest.json").write_text(
        json.dumps({"pass": all(c["pass"] for c in combined_checks),
                    "checks": combined_checks}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
