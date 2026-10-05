"""card_payment.py — is this statement row a monthly card payment?

A monthly card payment is a TRANSFER: it credits the card (a liability) and
debits the paying account (an asset). It is the one imported row whose
contra-leg is not an expense, and mis-posting it records repayment of a debt as
spending (`docs/adr/0007-imported-card-payment-is-a-transfer.md`).

The discriminator is `raw_data['is_card_payment']`, and it is set by the
ADAPTER at parse time, from the description the provider printed. That is the
only place the decision can be made: the flag has to exist before the row
reaches the writer, because the writer's whole job is to refuse the expense
path for a row carrying it.

Both v1 statement adapters that can produce one share this module, so there is
ONE list of patterns rather than one per adapter. It is `finance.ingestion`,
not `finance.ingestion.adapters`, and it imports nothing from an adapter, so
the two adapters can import it without a cycle.

**The pattern is not a name-based account lookup.** It matches the bank's own
wording for the settlement, exactly as `amex_pdf` has always done; it does not
pick an account, and it is not used to decide where the money went.

Measured against the real statements in `~/Documents/Banking`:

* Amex prints the payment as a credit with the description
  `HARTELIJK BEDANKT VOOR UW BETALING Kaartnummer ...` — 4 of 121 rows.
* Rabobank prints the debit that pays the card as
  `...8040 AMERICAN EXPRESS EUROPE S.A.` — 4 of 106 rows, on 2026-06-30,
  2026-07-30, 2026-08-28 and 2026-09-30, each matching the card's own credit
  0-2 days earlier.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["CARD_PAYMENT_PATTERNS", "is_card_payment"]


#: Each pattern is a provider's own wording for the same event. Ordered
#: longest-first is unnecessary; `any()` short-circuits and the patterns are
#: disjoint in practice.
#:
#: * `HARTELIJK BEDANKT VOOR UW BETALING` — the Amex card statement's credit.
#: * `AMERICAN EXPRESS EUROPE` — the Rabobank checking statement's debit. The
#:   printed description also carries a masked card number (`...8040`), which is
#:   why the match is on the merchant name rather than on the full line.
CARD_PAYMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"HARTELIJK\s+BEDANKT\s+VOOR\s+UW\s+BETALING", re.IGNORECASE),
    re.compile(r"AMERICAN\s+EXPRESS\s+EUROPE", re.IGNORECASE),
)


def is_card_payment(description: str) -> bool:
    """Whether a statement row's description is a monthly card settlement.

    Args:
        description: The row's description, as the adapter read it. Redaction
            does not remove the merchant wording, so either the raw or the
            redacted text is acceptable.

    Returns:
        True when any provider pattern matches. False otherwise — an
        unrecognised row is an ordinary expense until something says otherwise,
        never the reverse.
    """
    return any(pattern.search(description) for pattern in CARD_PAYMENT_PATTERNS)
