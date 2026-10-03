"""test_balance_invariant.py — the statement-balance identity, per account type.

No `source_record` or `journal_line` column stores a statement balance
(`P-NO-HISTORIC-BALANCE`, docs/research/11-real-export-verification.md §4), so
the identity is asserted nowhere in the schema and has to be asserted here
instead. It is worth an acceptance test because it is the cheapest completeness
check that exists: a parser that silently drops a row, mis-signs a credit or
drops an out-of-period row cannot balance, whatever else it gets right. During
the real-export verification it was the check that caught every parsing bug in
this exercise.

**There is no generic identity, and that is the point of this file.** The two
directions are opposite (doc 11 §3.3):

    current account   previous + credits - debits = closing
    card (Amex)       Vorig saldo + Debiteringen - Crediteringen = Nieuw saldo

A charge on a card *raises* the amount owed, because a card statement reports a
liability and a current account reports an asset. A single self-check applied to
both would pass one and fail the other while looking correct, so `AccountType`
is a required argument here and every adapter that ships has to state which
direction it satisfies (bead LifeOS-3pe item 4).

Coverage here is Amex only, honestly. `amex_pdf` is the one adapter in the
repository that parses a statement carrying an opening and a closing balance; the
`provider` CHECK values have no `rabobank_pdf` and no `revolut_pdf`
(ARCH:288-289, doc 11 §5.1-2), and neither AIS nor a CSV export states a
balance. So the current-account direction is stated against constructed rows and
labelled as such — see `TestCurrentAccountDirection`.

Every fixture is synthetic, as everywhere in this package. No real bank export
is in this repository and nothing here should be copied from one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from finance.ingestion.adapters import AmexPdfAdapter
from finance.ingestion.adapters.base import ImportResult
from finance.public import RawRecord

# Invented, and shaped like the BIGSERIAL M1 hands out. No real account id.
ACCOUNT_ID: Final = 1001


class AccountType(StrEnum):
    """Which direction a statement's balance moves. Not a style choice.

    Both identities are measured, not assumed: doc 11 §1 reconciles all four
    supplied Amex statements to delta 0, and §3.3 states the current-account
    direction against the Rabobank statements' own printed totals.
    """

    #: previous + credits - debits = closing. A payment out lowers the balance.
    ASSET = "asset"
    #: Vorig + Debiteringen - Crediteringen = Nieuw. A charge raises what is owed.
    LIABILITY = "liability"


def identity_closing(
    opening: int, signed_rows: Sequence[int], account_type: AccountType
) -> int:
    """The closing balance a statement of this account type must print.

    Rows are signed minor units under the project convention (ARCH:366, debits
    negative), which is what makes the two account types mirror images of each
    other on the same rows. `account_type` is therefore a required argument and
    not a default: defaulting it is how one adapter ends up validated against
    the other's identity.

    Args:
        opening: The statement's printed `Vorig saldo`, in minor units.
        signed_rows: The parsed rows, in minor units, debits negative.
        account_type: Which identity to apply.

    Returns:
        The closing balance the statement is required to print.
    """
    total = sum(signed_rows)
    if account_type is AccountType.ASSET:
        return opening + total
    return opening - total


# --- The synthetic Dutch Amex NL extract ------------------------------------
#
# Layout per the verified `pdftotext -layout` format: two DD.MM.YY dates per row
# (Transactiedatum, Datum verwerkt), an unsigned Dutch amount at end of line, and
# a bare `CR` line beneath a credit amount.

#: Dutch amount as printed: dot thousands, comma decimal.
_AMOUNT_RE: Final = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])")

#: The five summary labels share one line; the five values sit a few lines below.
_SUMMARY_LABELS: Final = ("Vorig saldo", "Crediteringen", "Debiteringen", "Nieuw saldo")

#: Vorig saldo carried in from the previous statement. Deliberately not derivable
#: from this period's rows — it is the one number the rows cannot produce, and it
#: is what stops the identity below from being `0 == 0`.
OPENING_MINOR: Final = 126019


@dataclass(frozen=True)
class StatementRow:
    """One printed transaction row, and the credit marker that may sit under it.

    `amount` is unsigned because that is what the statement prints: the PDF has no
    sign column at all (doc 11 §3.2). The only thing that makes a credit a credit
    is `is_credit`, which is written as a bare `CR` line beneath the amount.
    """

    booked: str
    processed: str
    description: str
    amount: str
    is_credit: bool = False
    continuation: tuple[str, ...] = ()


_ROWS: Final = (
    # Dated the day BEFORE the printed period opens. The Periode is advisory, not
    # a filter (doc 11 §5.3): real statements carry spend just outside it, and a
    # parser that trusts the bounds deletes this row.
    StatementRow("23.05.26", "24.05.26", "HOTEL DE ZEEHOND", "42,17"),
    # The monthly card payment. Printed unsigned, like a purchase; only the bare
    # CR line beneath it says otherwise, so losing that one line books the
    # payment as spend worth 721,35.
    StatementRow(
        "23.05.26",
        "24.05.26",
        "HARTELIJK BEDANKT VOOR UW BETALING",
        "721,35",
        is_credit=True,
    ),
    StatementRow("24.05.26", "25.05.26", "JUMBO 4321 AMSTERDAM", "8,50"),
    # A genuine refund, in the same credit section as the card payment. The two
    # are separable by description only (doc 11 §3.2), so a real statement mixes
    # them and the identity has to hold with both present.
    StatementRow("25.05.26", "26.05.26", "PAYPAL RAPPEL", "70,00", is_credit=True),
    StatementRow("26.05.26", "26.05.26", "NS INTERCITY", "34,90"),
    # A wrapped description. The continuation must append to the merchant, and it
    # must not become an amount or a row of its own.
    StatementRow(
        "27.05.26",
        "29.05.26",
        "AMAZON EU SARL",
        "99,99",
        continuation=("ORDER 123-4567890-1234567",),
    ),
)


@dataclass(frozen=True)
class PrintedSummary:
    """The five figures the statement prints on its own front page."""

    opening: int
    credits: int
    charges: int
    closing: int
    due: int


def _to_minor(dutch: str) -> int:
    """Dutch-formatted amount to unsigned minor units, by string surgery only."""
    digits = dutch.replace(".", "").replace(",", "")
    if not digits.isdigit():
        raise AssertionError(f"not a Dutch amount: {dutch!r}")
    return int(digits)


def _dutch(minor: int) -> str:
    """Minor units back to the format the statement prints."""
    sign = "-" if minor < 0 else ""
    whole, cents = divmod(abs(minor), 100)
    return f"{sign}{whole:,}".replace(",", ".") + f",{cents:02d}"


def amex_statement() -> list[str]:
    """The statement extract: a summary block, then the printed rows.

    `Nieuw saldo` is computed from `OPENING_MINOR` and the row definitions, never
    written as a literal. That is what makes the identity below real arithmetic —
    the closing balance is an independent claim by the statement, and the parsed
    rows have to reproduce it.
    """
    charges = sum(_to_minor(row.amount) for row in _ROWS if not row.is_credit)
    credits = sum(_to_minor(row.amount) for row in _ROWS if row.is_credit)
    closing = OPENING_MINOR + charges - credits

    summary_values = (
        f"{_dutch(OPENING_MINOR):>14}{_dutch(credits):>16}"
        f"{_dutch(charges):>16}{_dutch(closing):>16}{_dutch(closing):>16}"
    )
    lines = [
        "American Express Europe S.A. gevestigd in Amsterdam",
        "Kaartnummer ****4321",
        "Periode: 24.05.2026 tot 23.06.2026",
        "Nieuwe transacties voor:",
        "Vorig saldo        Crediteringen   Debiteringen    Nieuw saldo    Te betalen",
        "American Express Europe S.A. gevestigd in Amsterdam",
        summary_values,
        "Transactiedatum Datum verwerkt Omschrijving Bedrag",
    ]
    for row in _ROWS:
        amount = _dutch(_to_minor(row.amount))
        lines.append(
            f"{row.booked}  {row.processed}  {row.description:<48}{amount:>10}"
        )
        lines.extend(row.continuation)
        if row.is_credit:
            lines.append("CR")
    return lines


def printed_summary(lines: Sequence[str]) -> PrintedSummary:
    """Read the five printed figures back out of the extract.

    Read from the statement rather than from the builder's arithmetic, so a test
    cannot satisfy itself by comparing a value to itself.
    """
    for index, line in enumerate(lines):
        if all(label in line for label in _SUMMARY_LABELS):
            for candidate in lines[index + 1 : index + 8]:
                amounts = [_to_minor(found) for found in _AMOUNT_RE.findall(candidate)]
                if len(amounts) >= 5:
                    opening, credits, charges, closing, due = amounts[:5]
                    return PrintedSummary(opening, credits, charges, closing, due)
    raise AssertionError("fixture must print a five-value summary block")


def parse(lines: Sequence[str]) -> ImportResult:
    """Run the extract through the adapter's extraction seam.

    The extractor is injected as the existing adapter tests do it, so this file
    stays a string in, string out test and adds no PDF dependency.
    """
    adapter = AmexPdfAdapter(text_extractor=lambda _payload: list(lines))
    return adapter.parse("\n".join(lines).encode(), account_id=ACCOUNT_ID)


def signed_amounts(records: Sequence[RawRecord]) -> list[int]:
    """The parsed rows as signed minor units, which is what the identity sums."""
    return [record.amount_minor for record in records]


def identity_gap(
    lines: Sequence[str], records: Sequence[RawRecord], account_type: AccountType
) -> int:
    """How far the parsed rows miss the balance the statement printed.

    Zero means the identity holds. The size of the gap is the point of the
    regression tests below: it equals the magnitude of the row that was lost or
    mis-signed, so a test that merely asserted "the gap is non-zero" would pass
    for the wrong reason.
    """
    summary = printed_summary(lines)
    derived = identity_closing(summary.opening, signed_amounts(records), account_type)
    return summary.closing - derived


def _without(lines: Sequence[str], needle: str) -> list[str]:
    """A copy of the extract with every line containing `needle` deleted."""
    return [line for line in lines if needle not in line]


def _without_credit_marker(lines: Sequence[str], description: str) -> list[str]:
    """A copy of the extract with one row's bare `CR` line deleted.

    The defect this reproduces is LifeOS-3uq and LifeOS-zp7: the amount column
    carries no sign, so a lost CR line does not fail loudly — it books a card
    payment or a refund as a purchase and the statement still looks complete.
    """
    broken = list(lines)
    index = next(i for i, line in enumerate(broken) if description in line)
    assert broken[index + 1].strip() == "CR", "fixture row must carry its CR marker"
    del broken[index + 1]
    return broken


class TestAmexLiabilityIdentity:
    """The card direction: `Vorig + Debiteringen - Crediteringen = Nieuw saldo`.

    Verified against every supplied statement with delta 0 (doc 11 §1, §3.3), and
    a charge here *raises* what is owed — the opposite of a current account.
    """

    def test_parsed_rows_reconcile_to_the_printed_new_balance(self) -> None:
        """The acceptance test itself: every parsed row, against the statement.

        Asserting the printed closing alone would be vacuous; the sum comes from
        the parser and the closing comes from the document, so a disagreement
        means the parser misread something.
        """
        lines = amex_statement()
        result = parse(lines)

        assert not result.failed
        assert result.record_count == len(_ROWS)
        assert identity_gap(lines, result.records, AccountType.LIABILITY) == 0

    def test_the_fixture_is_not_a_trivial_identity(self) -> None:
        """Opening and closing must differ, or the check above proves nothing."""
        summary = printed_summary(amex_statement())
        assert summary.opening != summary.closing

    def test_charges_and_credits_match_the_printed_split(self) -> None:
        """The statement's own debit/credit split is fully recoverable.

        Not just the net: a parser that nets everything correctly while losing a
        charge and a credit of equal size would satisfy the closing balance and
        still be wrong (doc 11 §3.5 makes the same point for Rabobank).
        """
        lines = amex_statement()
        summary = printed_summary(lines)
        amounts = signed_amounts(parse(lines).records)

        charges = -sum(amount for amount in amounts if amount < 0)
        credits = sum(amount for amount in amounts if amount > 0)
        assert (charges, credits) == (summary.charges, summary.credits)

    def test_a_charge_is_a_debit_and_a_credit_is_a_credit(self) -> None:
        """The source→ledger sign flip, made explicit (ARCH:366, LifeOS-3pe item 2).

        Amex prints both unsigned, so the flip is the adapter's job. Losing it
        would put every purchase in the ledger as income with totals that still
        look plausible.
        """
        records = parse(amex_statement()).records
        by_description = {record.description: record for record in records}

        assert by_description["JUMBO 4321 AMSTERDAM"].amount_minor == -850
        assert by_description["PAYPAL RAPPEL"].amount_minor == 7000
        assert (
            by_description["HARTELIJK BEDANKT VOOR UW BETALING"].amount_minor == 72135
        )

    def test_the_card_payment_and_a_refund_are_both_credits(self) -> None:
        """The credit section of a real statement is not one kind of thing.

        The monthly card payment and a genuine refund are both bare `CR`, both
        unsigned, and are separable by description only (doc 11 §3.2). A fixture
        with only one of them would not exercise that.
        """
        records = parse(amex_statement()).records
        credits = [r for r in records if r.raw_data["is_credit"] is True]

        assert {record.description for record in credits} == {
            "HARTELIJK BEDANKT VOOR UW BETALING",
            "PAYPAL RAPPEL",
        }
        assert len(records) - len(credits) == len(_ROWS) - 2


class TestTheTwoDirectionsAreNotOneCheck:
    """§3.3's actual finding: one generic identity is wrong for one of the two.

    Every other test in this file would still pass if the account-type argument
    were quietly ignored, so this class is what stops that.
    """

    def test_the_card_statement_fails_the_current_account_direction(self) -> None:
        """The strongest statement of the difference, against a real parse.

        The same rows, the same printed opening and closing, the opposite formula:
        a generic check written for current accounts would reject every Amex
        statement this repo can import.
        """
        lines = amex_statement()
        records = parse(lines).records

        assert identity_gap(lines, records, AccountType.LIABILITY) == 0
        assert identity_gap(lines, records, AccountType.ASSET) != 0

    def test_the_gap_between_the_directions_is_twice_the_signed_total(self) -> None:
        """The two identities differ by a sign, so the miss is computable.

        Asserting the exact amount keeps this from becoming a tautology: any
        value would satisfy "not equal to zero".
        """
        lines = amex_statement()
        records = parse(lines).records
        total = sum(signed_amounts(records))

        gap = identity_gap(lines, records, AccountType.ASSET)
        assert gap == -2 * total
        # The period paid down 605,79 more than it spent, so a current-account
        # reading of this statement lands 1.211,58 away from what it printed.
        assert gap == -121158


class TestRowLevelDefectsBreakTheIdentity:
    """The regression guard: which row-level defect breaks the sum, and by how much.

    Each test breaks a *copy* of the extract, never the adapter, so a future
    parser change cannot quietly make these pass by agreeing with a broken
    fixture. The gap is asserted exactly, which is what proves the identity is
    sensitive to that specific row rather than to noise.
    """

    def test_a_dropped_row_breaks_the_identity(self) -> None:
        """A silently shortened import is the defect this identity exists for.

        LifeOS-4fy is exactly this: a parser that returns fewer rows without
        reporting an error. The gap equals the dropped row's own magnitude.
        """
        lines = amex_statement()
        result = parse(_without(lines, "JUMBO 4321 AMSTERDAM"))

        assert result.record_count == len(_ROWS) - 1
        assert identity_gap(lines, result.records, AccountType.LIABILITY) == 850

    def test_an_out_of_period_row_cannot_be_dropped(self) -> None:
        """The printed Periode is advisory, and filtering on it costs real spend.

        Doc 11 §5.3: real statements carry a transaction dated the day before the
        period opens. Dropping that row shortens the import without any error.
        """
        lines = amex_statement()
        result = parse(_without(lines, "HOTEL DE ZEEHOND"))

        assert result.record_count == len(_ROWS) - 1
        assert identity_gap(lines, result.records, AccountType.LIABILITY) == 4217

    def test_a_lost_credit_marker_breaks_the_identity(self) -> None:
        """LifeOS-3uq and LifeOS-zp7: the amount is unsigned, CR is the only sign.

        A dropped `CR` line flips one credit to a debit. The gap is twice the
        amount, because the row moved from the credit side to the debit side
        rather than merely losing its marker — and the card payment is the
        expensive instance of it, a monthly payment booked as a large purchase.
        """
        broken_cases = {
            "card payment": ("HARTELIJK BEDANKT VOOR UW BETALING", 72135),
            "refund": ("PAYPAL RAPPEL", 7000),
        }
        lines = amex_statement()
        for case, (description, amount) in broken_cases.items():
            records = parse(_without_credit_marker(lines, description)).records
            gap = identity_gap(lines, records, AccountType.LIABILITY)
            assert gap == -2 * amount, f"{case} lost its credit marker"
            assert records, case


class TestCurrentAccountDirection:
    """The current-account direction, stated honestly and labelled as unproven.

    `previous + credits - debits = closing`, measured against the Rabobank
    statements' own printed totals (doc 11 §3.3, §3.5).

    **No adapter here parses a current-account statement.** The `provider` CHECK
    values have no `rabobank_pdf` and no `revolut_pdf` (ARCH:288-289, doc 11
    §5.1-2), AIS transactions carry no balance, and no CSV export states one. So
    these rows are constructed rather than parsed, which means they demonstrate
    the direction and nothing about any parser. When a current-account adapter
    lands, it replaces these with the same identity applied to its own statement —
    inventing a fixture now would only make the coverage look complete.
    """

    #: A salary payment in, three bills and a card payment out. Debits negative,
    #: under ARCH:366. 1.000,00 + 2.500,00 - 42,17 - 8,50 - 34,90 - 250,00 =
    #: 3.164,43.
    ROWS: Final = (250000, -4217, -850, -3490, -25000)

    def test_credits_increase_a_current_account_balance(self) -> None:
        """The asset direction, with the arithmetic written out.

        The expected closing is a literal rather than `opening + sum(rows)`,
        which would only restate the implementation.
        """
        assert identity_closing(100000, self.ROWS, AccountType.ASSET) == 316443

    def test_the_liability_direction_is_wrong_for_a_current_account(self) -> None:
        """Applying the card formula to a current account gives the mirror image.

        The two identities cannot be reconciled by a sign convention or a
        constant offset: on the same rows they differ by twice the period total.
        """
        opening = 100000
        asset = identity_closing(opening, self.ROWS, AccountType.ASSET)
        liability = identity_closing(opening, self.ROWS, AccountType.LIABILITY)

        assert liability == 100000 - sum(self.ROWS)
        assert asset - liability == 2 * sum(self.ROWS) == 432886
