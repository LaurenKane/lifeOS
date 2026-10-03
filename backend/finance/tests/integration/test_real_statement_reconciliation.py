"""test_real_statement_reconciliation.py — the parsers against the real statements.

The statements these adapters were written for.

`test_balance_invariant.py` proves the *identity* against synthetic rows, which
is the right place to prove a formula. It cannot prove that the formula is the
one these banks actually print, that the column offsets are read off the right
page, or that a Dutch `1.234,56` and a Revolut `€5,378.27` are not the same
number. That is what this file is for: the nine real statements, the real
`pdftotext -layout` extraction, and the balance figures **as printed on the
document** checked against the rows the adapter produced.

The printed figures are read by this module's own readers, not by the adapters'
helpers. A test that reconciled `statement_totals()` against
`iter_transactions()` would compare one function with another and agree with a
parser that had drifted from the document. Every identity below is
*document -> parser*, never *parser -> parser*.

**Where the statements live, and why they are not in git.** They carry a real
name, a home address, the account's IBAN, a dozen counterparty IBANs, balances
and a full spending history, and the remote is a public GitHub URL. The user
chose local reference over committed fixtures, so:

* the files are read from `LIFEOS_STATEMENTS_DIR` (default
  `~/Documents/Banking`), which is outside this working tree;
* they are resolved by NAME from `STATEMENT_FILENAMES`, not globbed — see the
  comment on that constant for why, and `require_statements` for what happens
  when a name is missing or an unexpected statement turns up;
* nothing in this repository contains statement bytes, not even redacted
  copies;
* `.gitignore` carries rules for the statement filenames, so a copy dropped into
  the tree still cannot be committed. That class of tests runs whether or not
  the statements are present, so it is the one thing here that guards the
  repository rather than the parsers.

**Why these skip instead of failing.** CI and every other machine do not have
the statements, and a fixture that demanded them would make `make test` red
everywhere else. A skip here is not a green gate over nothing, though, and the
distinction matters: the *identity* is proved unconditionally by
`test_balance_invariant.py` against synthetic rows, so a skipped test here means
"not checked on this machine", not "not checked anywhere". Where the directory
is present but a provider's files are not, the skip reason names the provider
and the count it expected — a silently empty loop would read as "nothing to
reconcile" rather than "nothing to reconcile against".

**What this file does not prove.** It proves the three printed identities and
the month-to-month balance chains at delta 0, and it proves each identity is
*sensitive*: the sabotage classes break a copy of the extracted text and assert
the exact gap, because a reconciliation that cannot fail is worse than none. It
proves nothing about statement shapes this collection does not contain — a
column offset that never moves, a currency other than EUR, a credit section that
never mixes a card payment with a refund.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import pytest
from core.money import Currency, Money

from finance.ingestion.adapters import (
    AmexPdfAdapter,
    RabobankPdfAdapter,
    RevolutPdfAdapter,
)
from finance.ingestion.adapters.base import ImportResult
from finance.ingestion.adapters.pdf_text import extract_pdf_text, pdftotext_available
from finance.ingestion.adapters.revolut_pdf import strip_page_furniture
from finance.public import RawRecord

# ---------------------------------------------------------------------------
# Locating the statements
# ---------------------------------------------------------------------------

#: Overridable, so a machine that keeps them elsewhere can still run this.
STATEMENTS_DIR_ENV_VAR: Final = "LIFEOS_STATEMENTS_DIR"

#: Where they live on the machine these parsers were written on.
DEFAULT_STATEMENTS_DIR: Final = Path.home() / "Documents" / "Banking"

#: provider -> subdirectory of the statements root, named by the reference
#: implementation's own layout (`tools/bankparse/rabo.py --src`).
PROVIDER_DIRECTORIES: Final = {
    "amex": "Amex",
    "rabobank": "Rabo",
    "revolut": "Rev",
}

#: The statements this verification rests on, BY NAME.
#:
#: Named rather than globbed, so the set is a claim a reviewer can read instead
#: of a pattern that quietly matches whatever happens to be on disk. A glob over
#: `*.pdf` cannot tell "four statements" from "one statement plus three files
#: from another year", and the balance chain below is only a chain if it joins
#: four consecutive months.
#:
#: The Rabobank files arrived without extensions (they were downloads that were
#: never renamed) and were renamed to carry their month on 2026-10-03. Both shapes
#: are covered by `.gitignore` regardless, because a re-download can always come
#: back extensionless; the tests themselves name the current files.
#:
#: Amex names a file for the day it was GENERATED, not the period it covers, so
#: its name is a date and its period is printed inside — which is why the chain
#: below orders those statements by the printed `Periode`, never by the filename.
STATEMENT_FILENAMES: Final = {
    "rabobank": (
        "rabobank-2026-06.pdf",
        "rabobank-2026-07.pdf",
        "rabobank-2026-08.pdf",
        "rabobank-2026-09.pdf",
    ),
    "revolut": ("account-statement_2026-01-01_2026-10-01_en-gb_783080.pdf",),
    "amex": (
        "2026-06-23.pdf",
        "2026-07-23.pdf",
        "2026-08-23.pdf",
        "2026-09-23.pdf",
    ),
}

#: A PDF starts with `%PDF-`. Used to CHECK a named file rather than to find one,
#: so a truncated download or a zero-byte placeholder fails loudly here instead
#: of surfacing later as an unexplained `PdfTextExtractionError`.
PDF_MAGIC: Final = b"%PDF-"

#: How much of a file to read for that check. The header is at offset 0; this
#: only tolerates a producer that prefixes junk.
PDF_SNIFF_BYTES: Final = 1024

#: Invented ids, shaped like the BIGSERIAL M1 hands out. No real account id, and
#: nothing here is attributed to an account that exists.
ACCOUNT_ID: Final = 1001
SECOND_ACCOUNT_ID: Final = 1002

EUR: Final = Currency(code="EUR", name="Euro")


def statements_root() -> Path:
    """The configured statements directory, or a skip naming the env var."""
    configured = os.environ.get(STATEMENTS_DIR_ENV_VAR, "").strip()
    root = Path(configured).expanduser() if configured else DEFAULT_STATEMENTS_DIR
    if not root.is_dir():
        pytest.skip(
            f"no bank statements directory at {root}. Point "
            f"{STATEMENTS_DIR_ENV_VAR} at the directory holding the Amex/Rabo/Rev "
            "subdirectories; these tests reconcile the real statements and have "
            "nothing to reconcile without them."
        )
    return root


def _is_pdf(path: Path) -> bool:
    """Whether a file's *contents* are a PDF, whatever the file is called."""
    try:
        with path.open("rb") as handle:
            return PDF_MAGIC in handle.read(PDF_SNIFF_BYTES)
    except OSError:
        return False


def require_statements(provider: str) -> list[Path]:
    """The statements for `provider`, resolved by name, or a skip saying why not.

    A missing statement SKIPS: the files are not distributed with the
    repository, so their absence off this machine is expected rather than wrong,
    and the skip reason names both the provider and the file.

    An UNEXPECTED statement in the directory FAILS. A glob would absorb a fifth
    month and quietly lengthen the chain, or absorb a statement from another year
    and quietly break it; either way the suite would go on reporting the
    verification it was last configured for. Adding a month to the directory is
    a change to what this file claims, and the fix is one line above.
    """
    directory = statements_root() / PROVIDER_DIRECTORIES[provider]
    if not directory.is_dir():
        pytest.skip(
            f"no {directory}. Set {STATEMENTS_DIR_ENV_VAR} at the directory "
            "holding the Amex/Rabo/Rev subdirectories."
        )

    expected = STATEMENT_FILENAMES[provider]
    resolved: list[Path] = []
    absent: list[str] = []
    for name in expected:
        path = directory / name
        if not path.is_file():
            absent.append(name)
        elif not _is_pdf(path):
            pytest.fail(
                f"{path} is named as a statement but its contents are not a PDF "
                f"(no {PDF_MAGIC!r} in its first {PDF_SNIFF_BYTES} bytes). It is "
                "truncated, empty, or a placeholder."
            )
        else:
            resolved.append(path)
    if absent:
        pytest.skip(
            f"{provider}: {len(resolved)} of the {len(expected)} statements this "
            f"verification rests on are present; missing: {', '.join(absent)} "
            f"(expected under {directory})."
        )

    expected_set = set(expected)
    unexpected = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and _is_pdf(path) and path.name not in expected_set
    )
    assert not unexpected, (
        f"{provider}: {directory} holds statement(s) this file does not know "
        f"about: {unexpected}. Either add them to STATEMENT_FILENAMES so they "
        "are reconciled too, or move them out of the directory. A glob would "
        "have absorbed them silently, which is why the names are pinned."
    )
    return resolved


def require_extractor() -> None:
    """Skip when poppler is absent, naming the remedy rather than failing."""
    if not pdftotext_available():
        pytest.skip(
            "pdftotext (poppler-utils) is not installed, so these statements "
            "cannot be read. Install poppler-utils (Debian/Ubuntu: "
            "`apt-get install -y poppler-utils`); the other PDF tests inject a "
            "fake extractor and are unaffected."
        )


def statements(provider: str) -> list[tuple[str, Path]]:
    """`(filename, path)` for each statement, sorted, with poppler required."""
    require_extractor()
    return [(path.name, path) for path in require_statements(provider)]


def one_statement(provider: str) -> tuple[str, Path]:
    """The first statement of a provider. Revolut supplies exactly one."""
    return statements(provider)[0]


def lines_of(path: Path) -> list[str]:
    """The statement's text layer, through the real `pdftotext -layout` seam."""
    return extract_pdf_text(path.read_bytes())


# ---------------------------------------------------------------------------
# Amounts: minor units, and never a float
# ---------------------------------------------------------------------------

#: Dutch layout as printed: dot thousands, comma decimal. Neither the leading
#: digit group nor the decimal part may touch another digit, so a date or an
#: IBAN fragment inside a row cannot be read as an amount.
_DUTCH_AMOUNT: Final = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])")

#: Revolut prints the INVERSE: period decimal, comma thousands, symbol attached
#: and the sign outside it. Reusing the Dutch reader here would read `€5,378.27`
#: as 53782 — 100x wrong and still plausible-looking, which is why this is a
#: separate function with its own docstring rather than a flag.
_REVOLUT_AMOUNT: Final = re.compile(r"(-?)€\s*([\d.,]+)")

#: A printed balance carries its own direction word: `35,57 CR` or `620,59 DR`.
_PRINTED_BALANCE: Final = re.compile(r"([\d.,]+)\s*(CR|DR)\b")

#: Any printed IBAN. Used only to detect one leaking into a stored description.
_PRINTED_IBAN: Final = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,4}[A-Z0-9]{1,4}\b"
)

#: Letter-masked card numbers Amex prints (XXXX-XXXXXX-23007 or XXXXXXXXXX23007).
_MASKED_CARD_NUMBER: Final = re.compile(
    r"\b(?:[Xx]{4}-[Xx]{6}-\d{5}|[Xx]{10}\d{5})\b"
)


def dutch_to_money(text: str) -> Money:
    """`'1.234,56'` -> `Money(123456)`. Dot thousands, comma decimal.

    String surgery on the concatenated digits, never a float: rounding error
    compounds across a ledger, and a reconciliation is the one place it would be
    invisible.
    """
    digits = text.replace(".", "").replace(",", "")
    if not digits.isdigit():
        msg = f"not a Dutch-formatted amount: {text!r}"
        raise ValueError(msg)
    return Money(amount=int(digits), currency=EUR)


def revolut_to_money(text: str) -> Money:
    """`'€5,378.27'` -> `Money(537827)`. Period decimal, comma thousands."""
    cleaned = text.replace("€", "").strip().replace(" ", "")
    negative = cleaned.startswith("-")
    whole, dot, fraction = cleaned.lstrip("-").replace(",", "").partition(".")
    if not whole.isdigit() or (dot and not fraction.isdigit()):
        msg = f"not a Revolut-formatted amount: {text!r}"
        raise ValueError(msg)
    cents = int((fraction + "00")[:2])
    value = int(whole) * 100 + cents
    return Money(amount=-value if negative else value, currency=EUR)


def money_sum(amounts: Sequence[Money]) -> Money:
    """Total of same-currency amounts, seeded with `Money.zero`."""
    total = Money.zero(EUR)
    for amount in amounts:
        total = total + amount
    return total


def drop_gap(signed_minor: int, direction: Direction) -> Money:
    """The gap a dropped row of signed amount `signed_minor` must leave behind.

    Removing that row takes the signed total from `S` to `S - signed_minor`, so
    the derived closing moves by `-signed_minor` under `ASSET` and by
    `+signed_minor` under `LIABILITY`, and the gap moves the other way. Stated
    here so the expected number sits next to its reason instead of being
    re-derived, and therefore re-argued, at each call site.
    """
    amount = Money(amount=signed_minor, currency=EUR)
    return amount if direction is Direction.ASSET else -amount


class Direction(StrEnum):
    """Which way a statement's balance moves. Not a style choice.

    Repeated here rather than imported from `test_balance_invariant.py`, because
    a test module must not depend on another test module — but the reason it is
    spelled out at all is that file's: the two directions are opposite.

        current account (Rabobank, Revolut)  previous + credits - debits = closing
        card (Amex)      Vorig + Debiteringen - Crediteringen = Nieuw saldo
    """

    ASSET = "asset"
    LIABILITY = "liability"


def required_closing(
    opening: Money, signed_rows: Sequence[Money], direction: Direction
) -> Money:
    """The closing balance a statement of this account type has to print."""
    movement = money_sum(signed_rows)
    if direction is Direction.ASSET:
        return opening + movement
    return opening - movement


def signed_of(records: Sequence[RawRecord]) -> list[Money]:
    """The parsed rows as signed `Money`, which is what an identity sums."""
    return [Money(amount=record.amount_minor, currency=EUR) for record in records]


def debit_total(records: Sequence[RawRecord]) -> Money:
    """Parsed debits as a positive magnitude, to compare with a printed total."""
    return money_sum(
        [
            Money(amount=-r.amount_minor, currency=EUR)
            for r in records
            if r.amount_minor < 0
        ]
    )


def credit_total(records: Sequence[RawRecord]) -> Money:
    return money_sum(
        [
            Money(amount=r.amount_minor, currency=EUR)
            for r in records
            if r.amount_minor > 0
        ]
    )


def balance_gap(
    closing: Money, opening: Money, records: Sequence[RawRecord], direction: Direction
) -> Money:
    """How far the parsed rows miss the balance the document printed.

    Zero is the pass condition. The size of the gap is the point of the sabotage
    tests: it equals the magnitude of whatever was lost or mis-signed, so
    asserting only "not zero" would pass for the wrong reason.
    """
    return closing - required_closing(opening, signed_of(records), direction)


# ---------------------------------------------------------------------------
# Reading the printed figures, independently of the adapters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RabobankHeader:
    """The figures the Rabobank front page prints around the IBAN."""

    period_from: dt.date
    period_to: dt.date
    previous_balance: Money
    closing_balance: Money
    total_debited: Money
    total_credited: Money


@dataclass(frozen=True)
class AmexSummary:
    """The five figures the Amex front page prints above the rows."""

    previous: Money
    credits: Money
    charges: Money
    closing: Money
    due: Money


@dataclass(frozen=True)
class RevolutSummaryRow:
    """One row of the Revolut `Balance summary` table."""

    name: str
    opening: Money
    money_out: Money
    money_in: Money
    closing: Money


def _sole_line_with(lines: Sequence[str], needle: str) -> int:
    """The index of the one line containing `needle`, asserted to be unique.

    Ambiguity here means the reader is guessing, and a guessing reader would let
    a drifted parser agree with a broken document.
    """
    hits = [index for index, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        msg = f"{needle!r} appears on {len(hits)} lines, expected exactly 1"
        raise AssertionError(msg)
    return hits[0]


def _date_below(lines: Sequence[str], label: str) -> dt.date:
    """The `DD-MM-YYYY` printed under `label` on the statement's front page.

    Read from the FIRST occurrence, and the reason is printed layout rather than
    convenience: the front page states `From (date)` beside the account holder's
    postal address with its date on the next line, while each continuation page
    repeats both labels on one line above a row holding BOTH dates. Scanning for
    "the first date below `To (date)`" on a continuation page therefore returns
    the period's START date, so only the front-page arrangement is read. The
    label is matched anywhere in its line, not at its start, because on that page
    it shares the line with the address.
    """
    for index, line in enumerate(lines):
        if label not in line:
            continue
        for candidate in lines[index + 1 : index + 3]:
            match = re.search(r"\d{2}-\d{2}-\d{4}", candidate)
            if match is not None:
                return dt.datetime.strptime(match.group(0), "%d-%m-%Y").date()
    msg = f"no DD-MM-YYYY below {label!r}"
    raise AssertionError(msg)


def _printed_balance(lines: Sequence[str], label: str) -> Money:
    """A printed balance as an unsigned magnitude.

    The `CR`/`DR` word is matched and then discarded on purpose: rows are signed
    by column, so folding the direction word in here would apply the sign twice.
    Its presence is still asserted, because a balance printed without one is a
    different claim about the account's type.
    """
    index = _sole_line_with(lines, label)
    for candidate in lines[index + 1 : index + 3]:
        found = _PRINTED_BALANCE.search(candidate)
        if found is not None:
            return dutch_to_money(found.group(1))
    msg = f"no CR/DR balance below {label!r}"
    raise AssertionError(msg)


def rabobank_header(lines: Sequence[str]) -> RabobankHeader:
    """The printed period, both balances and both stated totals."""
    totals_index = _sole_line_with(lines, "Total amount debited")
    # Both totals are labelled on one header line and both printed on the next.
    stated = _DUTCH_AMOUNT.findall(lines[totals_index + 1])
    if len(stated) != 2:
        msg = f"expected exactly two stated totals, found {stated!r}"
        raise AssertionError(msg)
    return RabobankHeader(
        period_from=_date_below(lines, "From (date)"),
        period_to=_date_below(lines, "To (date)"),
        previous_balance=_printed_balance(lines, "Previous balance"),
        closing_balance=_printed_balance(lines, "Closing balance"),
        total_debited=dutch_to_money(stated[0]),
        total_credited=dutch_to_money(stated[1]),
    )


_AMEX_SUMMARY_LABELS: Final = (
    "Vorig saldo",
    "Crediteringen",
    "Debiteringen",
    "Nieuw saldo",
)


def amex_summary(lines: Sequence[str]) -> AmexSummary:
    """The five printed Amex figures, read off the value line positionally.

    That line prints as `721,35 - 791,35 + 835,67 = 765,67 765,67`: the signs sit
    BETWEEN the figures, so reading it as signed amounts would attribute the `-`
    to the wrong column. The figures are taken by position and the separators
    ignored, which is the only reading consistent with the labels above them.
    """
    labels = [
        index
        for index, line in enumerate(lines)
        if all(label in line for label in _AMEX_SUMMARY_LABELS)
    ]
    if len(labels) != 1:
        msg = f"the Amex summary header appears on {len(labels)} lines, expected 1"
        raise AssertionError(msg)
    for candidate in lines[labels[0] + 1 : labels[0] + 8]:
        found = _DUTCH_AMOUNT.findall(candidate)
        if len(found) >= 5:
            values = [dutch_to_money(text) for text in found[:5]]
            return AmexSummary(
                previous=values[0],
                credits=values[1],
                charges=values[2],
                closing=values[3],
                due=values[4],
            )
    msg = "no five-figure Amex summary line below the label row"
    raise AssertionError(msg)


def revolut_summary(lines: Sequence[str]) -> list[RevolutSummaryRow]:
    """The Revolut `Balance summary` table, one entry per printed row.

    Returned in printed order and including the `Total` row: the per-product
    identity and the total identity are different claims and both are asserted.
    """
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "Product" in line and "Opening balance" in line
        ),
        None,
    )
    if start is None:
        msg = "no Revolut balance-summary header found"
        raise AssertionError(msg)

    rows: list[RevolutSummaryRow] = []
    for line in lines[start:]:
        matches = list(_REVOLUT_AMOUNT.finditer(line))
        if len(matches) < 4:
            continue
        # The product name runs to the first amount. A column-boundary midpoint
        # would sit too far right and swallow the opening balance.
        name = re.sub(r"\s{2,}", " ", line[: matches[0].start()]).strip()
        if not name:
            continue
        values = [revolut_to_money(match.group(2)) for match in matches[:4]]
        rows.append(
            RevolutSummaryRow(
                name=name,
                opening=values[0],
                money_out=values[1],
                money_in=values[2],
                closing=values[3],
            )
        )
        if name == "Total":
            return rows
    msg = "the Revolut balance-summary table has no Total row"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Running the adapters
# ---------------------------------------------------------------------------


def _parse_rabobank(path: Path, filename: str) -> ImportResult:
    """Through the real extractor: this is the path production takes."""
    return RabobankPdfAdapter().parse(
        path.read_bytes(), account_id=ACCOUNT_ID, filename=filename
    )


def _parse_revolut(path: Path, filename: str) -> ImportResult:
    """One file, two products: the caller has to name an account per section."""
    return RevolutPdfAdapter(
        section_account_ids={"account": ACCOUNT_ID, "deposit": SECOND_ACCOUNT_ID}
    ).parse(path.read_bytes(), account_id=ACCOUNT_ID, filename=filename)


def _parse_amex(path: Path, filename: str) -> ImportResult:
    return AmexPdfAdapter().parse(
        path.read_bytes(), account_id=ACCOUNT_ID, filename=filename
    )


def _as_payload(lines: Sequence[str]) -> bytes:
    """Re-encode extracted text so an injected extractor can stand in for poppler."""
    return "\n".join(lines).encode("utf-8")


def _parse_rabobank_lines(lines: Sequence[str], filename: str) -> ImportResult:
    return RabobankPdfAdapter(text_extractor=lambda _payload: list(lines)).parse(
        _as_payload(lines), account_id=ACCOUNT_ID, filename=filename
    )


def _parse_revolut_lines(lines: Sequence[str], filename: str) -> ImportResult:
    return RevolutPdfAdapter(
        text_extractor=lambda _payload: list(lines),
        section_account_ids={"account": ACCOUNT_ID, "deposit": SECOND_ACCOUNT_ID},
    ).parse(_as_payload(lines), account_id=ACCOUNT_ID, filename=filename)


def _parse_amex_lines(lines: Sequence[str], filename: str) -> ImportResult:
    return AmexPdfAdapter(text_extractor=lambda _payload: list(lines)).parse(
        _as_payload(lines), account_id=ACCOUNT_ID, filename=filename
    )


def _no_failures(result: ImportResult, provider: str) -> None:
    """A row the parser could not read is a gap, not a rounding difference."""
    assert not result.failed, (
        f"{provider}: {len(result.failed)} row(s) reported unreadable, first at "
        f"line {result.failed[0].line_number}: {result.failed[0]}"
    )


def _signature(record: RawRecord) -> tuple[dt.date, int, str]:
    """What identifies a row across a parse of the same statement.

    Deliberately NOT `line_number`: deleting a line renumbers every row after
    it, so a line number cannot tell which row a sabotaged parse lost.
    """
    return (record.booked_date, record.amount_minor, record.description)


def _removed_amount(intact: ImportResult, sabotaged: ImportResult) -> int:
    """The signed amount of the single row `sabotaged` lost relative to `intact`.

    Compared as multisets, so a statement carrying two rows with the same date,
    amount and description still identifies the missing one: what the caller
    needs is its magnitude, and rows sharing a signature share it.

    Asserts the difference is exactly one row in exactly one direction. A
    sabotage that added rows, or removed several, would otherwise leave the
    expected gap as whatever the arithmetic happened to produce.
    """
    extra = Counter(_signature(r) for r in intact.records) - Counter(
        _signature(r) for r in sabotaged.records
    )
    missing = Counter(_signature(r) for r in sabotaged.records) - Counter(
        _signature(r) for r in intact.records
    )
    if len(extra) != 1 or missing:
        msg = (
            f"the sabotage removed {sum(extra.values())} row(s) and added "
            f"{sum(missing.values())}; expected exactly 1 removed and 0 added"
        )
        raise AssertionError(msg)
    (signature,) = tuple(extra)
    return signature[1]


# ---------------------------------------------------------------------------
# Rabobank
# ---------------------------------------------------------------------------

#: The per-page column header. Its character offsets ARE the anchors, and they
#: differ from page to page, which is why the adapter re-reads them per page.
_RABOBANK_HEADER: Final = re.compile(r"Debit amount\s+Credit amount")
#: A row opens with a `DD-MM` value date and a two-letter type code.
_RABOBANK_ROW: Final = re.compile(r"^\s*(\d{2})-(\d{2})\s+([a-z]{2})(\s|$)")


def _rabobank_header_index(lines: Sequence[str]) -> int:
    return next(
        index for index, line in enumerate(lines) if _RABOBANK_HEADER.search(line)
    )


def _rabobank_boundary(lines: Sequence[str], header_index: int) -> int:
    """The midpoint between the Debit and Credit anchors of one printed header."""
    anchors = _RABOBANK_HEADER.search(lines[header_index])
    if anchors is None:  # pragma: no cover - guarded by _rabobank_header_index
        msg = "no column header on the chosen line"
        raise AssertionError(msg)
    return (anchors.start() + anchors.end()) // 2


class TestRabobankPrintedTotalsReconcile:
    """`previous + credited - debited == closing`, per statement, delta 0.

    The printed `Total amount debited` / `Total amount credited` are asserted as
    well as the closing balance, because a parser that nets out correctly while
    losing a debit and a credit of equal size satisfies a closing balance and is
    still wrong.
    """

    def test_every_statement_reconciles_to_delta_zero(self) -> None:
        for name, path in statements("rabobank"):
            header = rabobank_header(lines_of(path))
            result = _parse_rabobank(path, name)

            _no_failures(result, "rabobank_pdf")
            assert result.record_count > 0, f"{name}: parsed no rows at all"
            assert debit_total(result.records) == header.total_debited, (
                f"{name}: parsed debits disagree with the printed "
                f"{header.total_debited}"
            )
            assert credit_total(result.records) == header.total_credited, (
                f"{name}: parsed credits disagree with the printed "
                f"{header.total_credited}"
            )
            gap = balance_gap(
                header.closing_balance,
                header.previous_balance,
                result.records,
                Direction.ASSET,
            )
            assert gap.amount == 0, (
                f"{name} ({header.period_from}..{header.period_to}): the parsed "
                f"rows miss the printed closing balance by {gap}"
            )

    def test_the_opening_and_closing_balances_differ(self) -> None:
        """A statement whose two balances are equal proves nothing by reconciling."""
        for name, path in statements("rabobank"):
            header = rabobank_header(lines_of(path))
            assert header.previous_balance != header.closing_balance, (
                f"{name}: previous and closing are both {header.previous_balance}"
            )

    def test_the_two_card_directions_cannot_both_hold(self) -> None:
        """The account type is load-bearing, not a detail of the formula.

        The same rows and the same printed opening balance satisfy the current
        account identity and miss the card one, so `Direction` cannot be an
        argument a caller is allowed to forget.
        """
        for name, path in statements("rabobank"):
            header = rabobank_header(lines_of(path))
            records = _parse_rabobank(path, name).records
            assert (
                balance_gap(
                    header.closing_balance,
                    header.previous_balance,
                    records,
                    Direction.ASSET,
                ).amount
                == 0
            )
            assert (
                balance_gap(
                    header.closing_balance,
                    header.previous_balance,
                    records,
                    Direction.LIABILITY,
                ).amount
                != 0
            )


class TestRabobankBalanceChain:
    """The strongest assertion available: month N's closing IS month N+1's opening.

    Four documents agreeing with each other is a different claim from one
    document agreeing with itself, and it catches what a single-statement
    identity cannot: a period read wrong, a statement missed entirely, two files
    swapped.
    """

    def test_closing_of_one_month_is_the_next_months_previous_balance(self) -> None:
        parsed = sorted(
            (rabobank_header(lines_of(path)).period_from, name, path)
            for name, path in statements("rabobank")
        )
        assert len(parsed) >= 2, "a chain needs at least two consecutive statements"

        for earlier, later in zip(parsed, parsed[1:], strict=False):
            earlier_header = rabobank_header(lines_of(earlier[2]))
            later_header = rabobank_header(lines_of(later[2]))
            assert later_header.period_from > earlier_header.period_from, (
                f"{earlier[1]} and {later[1]} are not in period order"
            )
            assert earlier_header.closing_balance == later_header.previous_balance, (
                f"{earlier[1]} closes at {earlier_header.closing_balance} but "
                f"{later[1]} opens at {later_header.previous_balance}"
            )

    def test_the_chain_binds_adjacent_statements_only(self) -> None:
        """Stop the chain from holding for a reason that proves nothing.

        If every closing equalled every other previous balance the previous test
        would pass without saying anything about *consecutive* periods. Measured
        against the real four, a statement two months away never carries the
        balance the first one closes at.
        """
        parsed = sorted(
            (rabobank_header(lines_of(path)).period_from, path)
            for name, path in statements("rabobank")
        )
        headers = [rabobank_header(lines_of(path)) for _, path in parsed]

        for index in range(len(headers) - 2):
            assert (
                headers[index].closing_balance != headers[index + 2].previous_balance
            ), (
                f"{headers[index].period_from} closes at "
                f"{headers[index].closing_balance}, which is also the previous "
                f"balance of {headers[index + 2].period_from}; the chain is not "
                "binding consecutive statements"
            )


class TestRabobankSabotage:
    """Which row-level defect breaks the identity, and by exactly how much.

    Each test breaks a COPY of the extracted text — never the file on disk, never
    the adapter — so a future parser change cannot make these pass by agreeing
    with a broken extract. The gap is asserted exactly, which is what makes the
    identity sensitive to that specific row rather than to noise.
    """

    def test_a_dropped_row_breaks_the_identity_by_that_rows_own_amount(self) -> None:
        """A silently shortened import is the defect this identity exists for.

        Dropping a row of signed amount `A` moves the gap by exactly `A`: the
        derived closing loses the row's whole contribution, so the size of the
        gap is the row's magnitude and its sign says which side of the account
        it was on.
        """
        name, path = one_statement("rabobank")
        lines = lines_of(path)
        header = rabobank_header(lines)

        row_line = next(
            index
            for index, line in enumerate(lines)
            if _RABOBANK_ROW.match(line) and _DUTCH_AMOUNT.search(line)
        )
        intact = _parse_rabobank_lines(lines, name)
        broken = lines[:row_line] + lines[row_line + 1 :]
        result = _parse_rabobank_lines(broken, f"{name} (one row deleted)")
        removed = _removed_amount(intact, result)

        assert result.record_count == intact.record_count - 1
        assert balance_gap(
            header.closing_balance,
            header.previous_balance,
            result.records,
            Direction.ASSET,
        ) == drop_gap(removed, Direction.ASSET)

    def test_a_mis_signed_row_breaks_the_identity_by_twice_its_amount(self) -> None:
        """The sign comes from the column offsets, so move one amount across them.

        The Debit/Credit anchors are re-read from the header of the page the row
        is on, which is the documented fragility: a row whose amount lands left
        of the midpoint is a debit whatever the bank printed. Rebuilding one
        credit row that way is the cheapest faithful version of that defect, and
        it costs twice the amount because the row moves from one side to the
        other rather than merely losing its sign.
        """
        name, path = one_statement("rabobank")
        lines = lines_of(path)
        header = rabobank_header(lines)

        header_index = _rabobank_header_index(lines)
        boundary = _rabobank_boundary(lines, header_index)
        row_line = next(
            index
            for index, line in enumerate(lines)
            for match in [_RABOBANK_ROW.match(line)]
            if match is not None
            and (amount := _DUTCH_AMOUNT.search(line)) is not None
            and amount.start() >= boundary
        )
        row_match = _RABOBANK_ROW.match(lines[row_line])
        amount = _DUTCH_AMOUNT.search(lines[row_line])
        assert row_match is not None and amount is not None
        anchor = lines[row_line][: row_match.end()]
        broken = list(lines)
        broken[row_line] = anchor + " " * (boundary - 1 - len(anchor)) + amount.group(1)

        result = _parse_rabobank_lines(broken, f"{name} (one row re-columned)")
        _no_failures(result, "rabobank_pdf")
        flipped = dutch_to_money(amount.group(1))
        assert (
            balance_gap(
                header.closing_balance,
                header.previous_balance,
                result.records,
                Direction.ASSET,
            )
            == 2 * flipped
        )


# ---------------------------------------------------------------------------
# Revolut
# ---------------------------------------------------------------------------

#: The transaction table's per-page header. Re-read on every occurrence, and
#: its offsets differ per page — the Revolut form of the same column problem.
_REVOLUT_HEADER: Final = re.compile(
    r"Description.*Money\s+out.*Money\s+in.*Balance", re.I
)
_REVOLUT_ROW: Final = re.compile(r"^\s*(\d{1,2})\s+([A-Z][a-z]{2,4})\s+(\d{4})\s{2,}")


def _revolut_body(lines: Sequence[str]) -> list[str]:
    """The statement with its repeated legal footer and running header removed.

    The adapter strips this before it numbers lines, so a sabotage that edits a
    line by index has to strip it too or it edits the wrong line. Stripping is
    idempotent, which is what makes re-parsing the stripped body safe.
    """
    kept, _dropped = strip_page_furniture(list(lines))
    return kept


def _revolut_products(records: Sequence[RawRecord]) -> dict[str, list[RawRecord]]:
    """Parsed rows grouped by the product the adapter attributed them to."""
    grouped: dict[str, list[RawRecord]] = {}
    for record in records:
        grouped.setdefault(str(record.raw_data.get("product", "")), []).append(record)
    return grouped


class TestRevolutBalanceSummaryReconcile:
    """`opening - money out + money in == closing`, per product AND for the total.

    Revolut prints all four figures itself, so this is the same kind of identity
    as the other two providers with an extra row: `Total` is a separate claim
    about every row together, and a parser that reconciled each product while
    dropping a row from one of them would still satisfy the per-product checks.
    """

    #: Summary label as printed -> the product name the adapter reports. They
    #: differ on the current account on purpose: the statement prints
    #: `Account (Current Account)` and the adapter reports `Current Account`, so
    #: this mapping is itself a claim about how the two are related.
    PRINTED_TO_PRODUCT: Final = {
        "Account (Current Account)": "Current Account",
        "Deposit": "Deposit",
    }

    def test_the_summary_table_states_every_product_and_a_total(self) -> None:
        _name, path = one_statement("revolut")
        rows = revolut_summary(lines_of(path))

        assert rows[-1].name == "Total", "the balance summary must end at its Total"
        assert {row.name for row in rows} == {*self.PRINTED_TO_PRODUCT, "Total"}, (
            f"unexpected balance-summary rows: {[row.name for row in rows]}"
        )

    def test_every_product_reconciles_to_delta_zero(self) -> None:
        _name, path = one_statement("revolut")
        summary = {row.name: row for row in revolut_summary(lines_of(path))}
        result = _parse_revolut(path, path.name)

        _no_failures(result, "revolut_pdf")
        grouped = _revolut_products(result.records)

        for printed, product in self.PRINTED_TO_PRODUCT.items():
            assert product in grouped, f"no parsed rows for product {product!r}"
            records = grouped[product]
            row = summary[printed]
            assert debit_total(records) == row.money_out, (
                f"{product}: parsed money out disagrees with the printed "
                f"{row.money_out}"
            )
            assert credit_total(records) == row.money_in, (
                f"{product}: parsed money in disagrees with the printed {row.money_in}"
            )
            gap = balance_gap(row.closing, row.opening, records, Direction.ASSET)
            assert gap.amount == 0, (
                f"{product}: the parsed rows miss the printed closing balance by {gap}"
            )

    def test_the_total_row_reconciles_over_every_row(self) -> None:
        _name, path = one_statement("revolut")
        total = {row.name: row for row in revolut_summary(lines_of(path))}["Total"]
        result = _parse_revolut(path, path.name)

        assert debit_total(result.records) == total.money_out, (
            "every parsed row disagrees with the printed Total money out"
        )
        assert credit_total(result.records) == total.money_in, (
            "every parsed row disagrees with the printed Total money in"
        )
        gap = balance_gap(total.closing, total.opening, result.records, Direction.ASSET)
        assert gap.amount == 0, (
            f"every parsed row misses the printed Total closing balance by {gap}"
        )

    def test_the_card_direction_is_wrong_for_this_statement(self) -> None:
        _name, path = one_statement("revolut")
        total = {row.name: row for row in revolut_summary(lines_of(path))}["Total"]
        records = _parse_revolut(path, path.name).records

        assert (
            balance_gap(total.closing, total.opening, records, Direction.ASSET).amount
            == 0
        )
        assert (
            balance_gap(
                total.closing, total.opening, records, Direction.LIABILITY
            ).amount
            != 0
        )


class TestRevolutSabotage:
    """The Revolut identities are sensitive to a dropped row and to a moved one."""

    def test_a_dropped_row_breaks_the_identity_by_that_rows_own_amount(self) -> None:
        _name, path = one_statement("revolut")
        lines = lines_of(path)
        total = {row.name: row for row in revolut_summary(lines)}["Total"]

        body = _revolut_body(lines)
        row_line = next(
            index for index, line in enumerate(body) if _REVOLUT_ROW.match(line)
        )
        intact = _parse_revolut_lines(body, path.name)
        broken = body[:row_line] + body[row_line + 1 :]
        result = _parse_revolut_lines(broken, f"{path.name} (one row deleted)")
        _no_failures(result, "revolut_pdf")
        removed = _removed_amount(intact, result)

        assert result.record_count == intact.record_count - 1
        assert balance_gap(
            total.closing, total.opening, result.records, Direction.ASSET
        ) == drop_gap(removed, Direction.ASSET)

    def test_a_mis_columned_row_breaks_the_identity_by_twice_its_amount(self) -> None:
        """Money out and money in are separate columns; shift one across the anchor.

        The anchors come from the printed header of the page the row is on, and
        an amount is classified by the nearest preceding anchor, so pushing one
        money-out amount a column to the right books it as money in. That is
        worth twice the amount, because the row crosses to the other side.
        """
        _name, path = one_statement("revolut")
        lines = lines_of(path)
        total = {row.name: row for row in revolut_summary(lines)}["Total"]

        body = _revolut_body(lines)
        header_line = next(
            index for index, line in enumerate(body) if _REVOLUT_HEADER.search(line)
        )
        # The boundary between two columns is the midpoint BETWEEN their printed
        # labels, not the label's own offset: an amount is read as belonging to
        # the nearest label that starts after it, so the label position itself is
        # still inside the previous column.
        out_label = body[header_line].index("Money out")
        in_label = body[header_line].index("Money in")
        in_bound = (in_label + body[header_line].index("Balance")) // 2
        out_bound = (out_label + in_label) // 2
        assert out_label < in_label, "the printed columns are not in order"

        row_line, amount = next(
            (index, _REVOLUT_AMOUNT.search(line))
            for index, line in enumerate(body)
            if _REVOLUT_ROW.match(line)
            and _REVOLUT_AMOUNT.search(line) is not None
            and _REVOLUT_AMOUNT.search(line).start() < out_bound  # type: ignore[union-attr]
        )
        assert amount is not None
        pad = out_bound - amount.start()
        assert pad > 0, "the chosen row's amount is not in the Money out column"
        assert out_bound <= amount.start() + pad < in_bound, (
            "the shifted amount must land in the Money in column, or this tests "
            "column drift rather than a sign flip"
        )
        broken = list(body)
        broken[row_line] = (
            body[row_line][: amount.start()]
            + " " * pad
            + body[row_line][amount.start() :]
        )

        result = _parse_revolut_lines(broken, f"{path.name} (one row re-columned)")
        _no_failures(result, "revolut_pdf")
        moved = revolut_to_money(amount.group(0))
        assert (
            balance_gap(total.closing, total.opening, result.records, Direction.ASSET)
            == -2 * moved
        )


# ---------------------------------------------------------------------------
# Amex
# ---------------------------------------------------------------------------

#: An Amex row: two `DD.MM.YY` dates, then the description, then the amount.
_AMEX_ROW: Final = re.compile(r"^\s*\d{2}\.\d{2}\.\d{2}\s+\d{2}\.\d{2}\.\d{2}\s+\S")
#: The bare `CR` printed beneath an amount. Amex prints no sign, so this line is
#: the only thing that separates a refund from a purchase.
_AMEX_CREDIT_MARKER: Final = re.compile(r"^\s*CR\s*$")


def _amex_credit_marker_amount(lines: Sequence[str]) -> Money:
    """The amount of the row carrying the first bare `CR` marker."""
    for index, line in enumerate(lines):
        if not _AMEX_CREDIT_MARKER.match(line):
            continue
        for candidate in reversed(lines[:index]):
            if _AMEX_ROW.match(candidate):
                amount = _DUTCH_AMOUNT.search(candidate.rstrip())
                if amount is not None:
                    return dutch_to_money(amount.group(1))
    msg = "no CR marker with a transaction row above it"
    raise AssertionError(msg)


#: `Periode: 24.05.2026 tot 23.06.2026`, read to ORDER the statements. Ordering
#: by filename would be a mistake twice over: Amex names a file for the day it
#: was generated rather than the period it covers, and three of the Rabobank
#: files carry no extension at all. The period is printed on the document, so
#: the document is where the order comes from.
_AMEX_PERIOD: Final = re.compile(
    r"Periode:\s*(\d{2}\.\d{2}\.\d{4})\s+tot\s+(\d{2}\.\d{2}\.\d{4})"
)


def _amex_period_end(lines: Sequence[str]) -> dt.date:
    """The last day of the period the statement prints for itself."""
    text = "\n".join(lines)
    match = _AMEX_PERIOD.search(text)
    if match is None:
        msg = "the Amex statement prints no `Periode: <from> tot <to>` line"
        raise AssertionError(msg)
    return dt.datetime.strptime(match.group(2), "%d.%m.%Y").date()


class TestAmexLiabilityReconcile:
    """`Vorig + Debiteringen - Crediteringen = Nieuw saldo`, per statement, delta 0.

    A charge on a card *raises* what is owed, so the sign runs the other way from
    the other two providers. `Te betalen` is asserted too: it is a separate claim
    by the document, and a parser that reproduces the balance while misreading the
    amount due has still misread something.
    """

    def test_every_statement_reconciles_to_delta_zero(self) -> None:
        for name, path in statements("amex"):
            summary = amex_summary(lines_of(path))
            result = _parse_amex(path, name)

            _no_failures(result, "amex_pdf")
            assert result.record_count > 0, f"{name}: parsed no rows at all"
            assert debit_total(result.records) == summary.charges, (
                f"{name}: parsed charges disagree with the printed Debiteringen "
                f"{summary.charges}"
            )
            assert credit_total(result.records) == summary.credits, (
                f"{name}: parsed credits disagree with the printed Crediteringen "
                f"{summary.credits}"
            )
            gap = balance_gap(
                summary.closing, summary.previous, result.records, Direction.LIABILITY
            )
            assert gap.amount == 0, (
                f"{name}: the parsed rows miss the printed Nieuw saldo by {gap}"
            )

    def test_the_amount_due_equals_the_new_balance(self) -> None:
        for name, path in statements("amex"):
            summary = amex_summary(lines_of(path))
            assert summary.due == summary.closing, (
                f"{name}: Te betalen {summary.due} != Nieuw saldo {summary.closing}"
            )

    def test_the_card_payment_is_a_credit_and_not_a_purchase(self) -> None:
        """The monthly payment shares the credit section with real refunds.

        Both are printed unsigned and are separable by description only, so a
        statement whose largest row is that payment is the instance where a lost
        `CR` marker costs the most.
        """
        for name, path in statements("amex"):
            payments = [
                record
                for record in _parse_amex(path, name).records
                if record.raw_data.get("is_card_payment") is True
            ]
            assert payments, f"{name}: no card payment among the parsed rows"
            for payment in payments:
                assert payment.amount_minor > 0, (
                    f"{name}: the card payment booked as a charge of "
                    f"{payment.amount_minor}"
                )


class TestAmexBalanceChain:
    """Closing of statement N is `Vorig saldo` of statement N+1, as for Rabobank."""

    def test_each_statement_opens_where_the_previous_one_closed(self) -> None:
        parsed = sorted(
            (_amex_period_end(lines_of(path)), name, path)
            for name, path in statements("amex")
        )
        summaries = [amex_summary(lines_of(path)) for _, _, path in parsed]
        assert summaries, "no Amex statements to chain"

        for index, (earlier, later) in enumerate(
            zip(summaries, summaries[1:], strict=False)
        ):
            assert parsed[index + 1][0] > parsed[index][0], (
                f"{parsed[index][1]} and {parsed[index + 1][1]} are not in period order"
            )
            assert later.previous == earlier.closing, (
                f"{parsed[index][1]} closes at {earlier.closing} but "
                f"{parsed[index + 1][1]} opens at {later.previous}"
            )


class TestAmexSabotage:
    """A lost `CR` marker is the defect this identity was written for."""

    def test_a_lost_credit_marker_breaks_the_identity_by_twice_its_amount(self) -> None:
        name, path = one_statement("amex")
        lines = lines_of(path)
        summary = amex_summary(lines)

        marker = next(
            index for index, line in enumerate(lines) if _AMEX_CREDIT_MARKER.match(line)
        )
        lost = _amex_credit_marker_amount(lines)
        broken = lines[:marker] + lines[marker + 1 :]

        result = _parse_amex_lines(broken, f"{name} (one CR marker deleted)")
        _no_failures(result, "amex_pdf")
        # The row moved from the credit side to the debit side rather than merely
        # losing its marker, which is why it costs twice the amount.
        assert (
            balance_gap(
                summary.closing, summary.previous, result.records, Direction.LIABILITY
            )
            == -2 * lost
        )

    def test_a_dropped_row_breaks_the_identity_by_that_rows_own_amount(self) -> None:
        name, path = one_statement("amex")
        lines = lines_of(path)
        summary = amex_summary(lines)

        intact = _parse_amex_lines(lines, name)
        # A charge, not a credit: deleting a credit row would orphan its bare
        # `CR` line, which would then mark the NEXT row as a credit and change
        # two rows at once. That is a different defect, tested above.
        row_line = next(
            index
            for index, line in enumerate(lines)
            if _AMEX_ROW.match(line)
            and _DUTCH_AMOUNT.search(line.rstrip())
            and not _AMEX_CREDIT_MARKER.match(lines[index + 1])
        )
        broken = lines[:row_line] + lines[row_line + 1 :]
        result = _parse_amex_lines(broken, f"{name} (one row deleted)")
        _no_failures(result, "amex_pdf")
        removed = _removed_amount(intact, result)

        assert removed < 0, (
            "the sabotage must delete a charge, so no `CR` line is orphaned"
        )
        assert result.record_count == intact.record_count - 1
        assert balance_gap(
            summary.closing, summary.previous, result.records, Direction.LIABILITY
        ) == drop_gap(removed, Direction.LIABILITY)


# ---------------------------------------------------------------------------
# The statements must never be committable
# ---------------------------------------------------------------------------


class TestTheStatementsCannotBeCommitted:
    """The `.gitignore` rules, enforced rather than trusted.

    Every other test in this file is skip-if-absent, so it says nothing about
    whether the statements could reach the remote. This class runs
    unconditionally — it reads no statement — and it is the only test here that
    fails on a machine that does not have them.

    `git check-ignore` is asked rather than `.gitignore` being parsed, because
    the question is what git does with a path, and reimplementing gitignore
    semantics in a test would be a second, wronger answer. Paths are checked
    relative to the repository root, so the answer does not depend on where
    pytest was started from.
    """

    REPO_ROOT: Final = Path(__file__).resolve().parents[4]

    #: Every shape the statements are known to arrive in, which is not the same
    #: as the files currently on disk. Three of the four Rabobank statements were
    #: originally downloads with no extension at all, so `.gitignore` must keep
    #: covering that shape whatever it is called today: a re-download can always
    #: arrive extensionless, and the rules cost nothing. This list pins the RULES,
    #: not the current filenames.
    STATEMENT_PATHS: Final = (
        "Banking/Rabo/rabobank-2026-06.pdf",
        "Rabo Banking details",
        "Rabo Banking details - 1Pwg8D7YSWHMyt1-m0rQ_dv9AC7v0UKHK",
        "Rabo Banking details September.pdf",
        "Rabo/rabobank-2026-06.pdf",
        "rabobank-2026-08.pdf",
        "Rev/account-statement_2026-01-01_2026-10-01_en-gb_783080.pdf",
        "Amex/2026-06-23.pdf",
        "2026-09-23.pdf",
        "docs/research/nested/Rabo Banking details",
    )

    #: Project files that must stay visible. A rule broad enough to swallow a
    #: statement is easy to write and easy to write too widely; these are the
    #: files that would go missing if it were.
    MUST_STAY_TRACKABLE: Final = (
        "backend/finance/tests/integration/test_real_statement_reconciliation.py",
        "docs/adr/0002-import-provider-enum.md",
        "backend/finance/ingestion/adapters/rabobank_pdf.py",
    )

    @staticmethod
    def _check_ignore(paths: Sequence[str]) -> dict[str, str]:
        """`path -> the rule that decides it`, for paths git ignores.

        `--no-index` so an already-tracked file is judged by the ignore rules
        rather than by what is in the index: the question here is what would
        happen to a file about to be added, not what happened to one already
        committed.
        """
        try:
            completed = subprocess.run(
                ["git", "check-ignore", "-v", "--no-index", "--", *paths],
                cwd=TestTheStatementsCannotBeCommitted.REPO_ROOT,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"git is unavailable, so .gitignore cannot be checked: {exc}")
        if completed.returncode not in (0, 1):
            pytest.skip(
                f"git check-ignore failed ({completed.returncode}): "
                f"{completed.stderr.decode(errors='replace').strip()}"
            )
        decided: dict[str, str] = {}
        for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
            # `-v` prints "<source>:<line>:<pattern>\t<pathname>". Split on the TAB,
            # not on colons: a pattern such as `**/Banking/` has none, but
            # `20??-??-??.pdf` and `docs/adr/...` shapes can, and a colon-split
            # would silently mis-key the map.
            rule, tab, pathname = line.partition("\t")
            if not tab:
                continue
            fields = rule.split(":", 2)
            if len(fields) == 3:
                decided[pathname] = f"{fields[0]}:{fields[1]}: {fields[2]}"
        return decided

    def test_every_known_statement_path_is_ignored(self) -> None:
        decided = self._check_ignore(self.STATEMENT_PATHS)

        exposed = [path for path in self.STATEMENT_PATHS if path not in decided]
        assert not exposed, (
            f"{len(exposed)} statement path(s) are NOT ignored by .gitignore and "
            f"could be committed to a public remote: {exposed}. They carry a "
            "real name, home address, IBANs and a full spending history."
        )

    def test_the_rules_do_not_swallow_project_files(self) -> None:
        """A rule wide enough to hide a statement may also hide the code."""
        decided = self._check_ignore(self.MUST_STAY_TRACKABLE)

        hidden = [path for path in self.MUST_STAY_TRACKABLE if path in decided]
        assert not hidden, f".gitignore is hiding tracked project files: {hidden}"

    def test_no_statement_file_is_already_tracked(self) -> None:
        """The rules are the second line of defence, not the first.

        `.gitignore` does not un-track anything, so a statement committed before
        these rules existed would still be in the history and still be on the
        remote. This asks git directly rather than trusting the rules above.
        """
        try:
            completed = subprocess.run(
                ["git", "ls-files"],
                cwd=self.REPO_ROOT,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"git is unavailable, so tracked files cannot be listed: {exc}")
        assert completed.returncode == 0, (
            f"git ls-files failed: {completed.stderr.decode(errors='replace')}"
        )
        tracked = completed.stdout.decode("utf-8", errors="replace").splitlines()
        leaked = [
            name
            for name in tracked
            if name.endswith(".pdf")
            or "Rabo Banking details" in name
            or name.startswith(("Banking/", "Rabo/", "Rev/", "Amex/"))
        ]
        assert not leaked, (
            f"{len(leaked)} statement-shaped file(s) are already tracked and "
            f"would be published by any push: {leaked}"
        )


# ---------------------------------------------------------------------------
# Privacy: no full IBAN or cardholder details reach the immutable raw_data
# ---------------------------------------------------------------------------


def _records_with_full_iban(
    records: Sequence[RawRecord], provider: str, filename: str
) -> list[tuple[str, str, str]]:
    """Any `(provider, filename, matched_iban)` found in a provider's records."""
    offenders: list[tuple[str, str, str]] = []
    for record in records:
        for field in (record.description, str(record.raw_data)):
            found = _PRINTED_IBAN.search(field)
            if found is not None:
                offenders.append((provider, filename, found.group(0)))
    return offenders


class TestPrivacy:
    """The immutable raw_data column must never store a full IBAN or PII."""

    def test_no_amex_description_carries_a_full_iban(self) -> None:
        require_extractor()
        offenders: list[tuple[str, str]] = []
        for name, path in statements("amex"):
            for record in _parse_amex(path, name).records:
                candidates = (
                    record.description,
                    str(record.raw_data.get("description", "")),
                )
                for field in candidates:
                    found = _PRINTED_IBAN.search(field)
                    if found is not None:
                        offenders.append((name, found.group(0)))
        assert not offenders, (
            f"{len(offenders)} Amex description(s) carry a full IBAN, which "
            f"raw_data stores forever; first: {offenders[0] if offenders else ''}"
        )

    def test_no_amex_description_carries_the_legal_block(self) -> None:
        """The trailing legal block must not be appended to any real row."""
        require_extractor()
        offenders: list[tuple[str, str]] = []
        for name, path in statements("amex"):
            for record in _parse_amex(path, name).records:
                lowered = record.description.lower()
                for marker in (
                    "belangrijke informatie",
                    "bankgegevens",
                    "correspondentieadres",
                    "iban:",
                    "bic:",
                ):
                    if marker in lowered:
                        offenders.append((name, marker))
                        break
        assert not offenders, (
            f"{len(offenders)} Amex description(s) still carry legal-block prose; "
            f"first marker: {offenders[0] if offenders else ''}"
        )

    def test_no_adapter_description_or_raw_data_carries_a_full_iban(self) -> None:
        """All three PDF adapters mask IBANs before anything reaches raw_data."""
        require_extractor()
        offenders: list[tuple[str, str, str]] = []
        for name, path in statements("rabobank"):
            records = _parse_rabobank(path, name).records
            offenders.extend(_records_with_full_iban(records, "rabobank", name))
        for name, path in statements("revolut"):
            records = _parse_revolut(path, name).records
            offenders.extend(_records_with_full_iban(records, "revolut", name))
        for name, path in statements("amex"):
            records = _parse_amex(path, name).records
            offenders.extend(_records_with_full_iban(records, "amex", name))
        assert not offenders, (
            f"{len(offenders)} record field(s) carry a full IBAN, which raw_data "
            f"stores forever; first: {offenders[0] if offenders else ''}"
        )

    def test_no_amex_description_carries_cardholder_or_rewards_details(self) -> None:
        """The page 4/4 trailing block must not leak the cardholder or card number."""
        require_extractor()
        offenders: list[tuple[str, str]] = []
        for name, path in statements("amex"):
            for record in _parse_amex(path, name).records:
                candidates = (
                    record.description,
                    str(record.raw_data.get("description", "")),
                )
                for field in candidates:
                    lowered = field.lower()
                    if "mevr " in lowered:
                        offenders.append((name, "MEVR "))
                        break
                    if "membership rewards" in lowered:
                        offenders.append((name, "Membership Rewards"))
                        break
                    masked = _MASKED_CARD_NUMBER.search(field)
                    if masked:
                        offenders.append((name, masked.group(0)))
                        break
        assert not offenders, (
            f"{len(offenders)} Amex description(s) still carry cardholder or "
            f"rewards details; first: {offenders[0] if offenders else ''}"
        )


class TestTheRuntimeImageKeepsTheExtractor:
    """`pdftotext` is a system binary, so pip cannot express the dependency.

    Everything above runs against whatever `pdftotext` happens to be on the
    machine running pytest. That is a real gap and it was live for a while:
    `backend/finance/api/routes/imports.py` documented that the runtime image
    installs poppler-utils, and `backend/Dockerfile` did not. Every test passed
    on a developer host that had poppler, while every PDF upload in the actual
    container failed with "pdftotext is not installed".

    A behavioural test cannot catch that, because the seam is injectable and the
    tests inject it. So this asserts the dependency statically instead. It is a
    text check, deliberately: the alternative is building the image in CI, which
    is a different and much more expensive thing to own.
    """

    REPO_ROOT: Final = Path(__file__).resolve().parents[4]
    DOCKERFILE: Final = REPO_ROOT / "backend" / "Dockerfile"

    def _apt_install_line(self) -> str:
        text = self.DOCKERFILE.read_text(encoding="utf-8")
        installs = [
            line
            for line in text.splitlines()
            if "apt-get install" in line and "rm -rf" not in line
        ]
        assert installs, "backend/Dockerfile has no apt-get install line at all"
        return " ".join(installs)

    def test_the_runtime_image_installs_poppler_utils(self) -> None:
        assert "poppler-utils" in self._apt_install_line(), (
            "backend/Dockerfile does not install poppler-utils. Every PDF "
            "upload will fail in the container with 'pdftotext is not "
            "installed' while passing in tests, which exercise the host binary."
        )

    def test_the_extractor_names_the_binary_it_needs(self) -> None:
        extractor = (
            self.REPO_ROOT
            / "backend"
            / "finance"
            / "ingestion"
            / "adapters"
            / "pdf_text.py"
        ).read_text(encoding="utf-8")
        assert "pdftotext" in extractor, (
            "pdf_text.py is the extraction seam; it must state the binary it "
            "shells out to, so the Dockerfile dependency above is traceable to "
            "the code that needs it"
        )
