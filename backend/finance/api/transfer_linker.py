"""transfer_linker.py — the transfer-matching sweep over stored journal lines.

The PURE rule lives in `finance.domain.services.transfer_match`
(`JournalLineRef`, `transfer_match`, `transfer_window`) and is reused here,
never reimplemented. That module stays pure — no database — so the candidate
query lives here, where a session exists.

The sweep looks for the unmatched OUTGOING leg (negative amount on an
asset/liability account) and searches for its incoming half in a 1-day-before
to 3-days-after window. Exactly one exact same-currency same-day candidate
links automatically; anything weaker is a QUESTION for the review queue, never
a guess — a wrong link silently turns spending into a transfer and destroys
the breakdown the user actually wants. Cross-currency pairs NEVER auto-link.

The caller owns the transaction (`with session.begin():` around the call),
for the same reason every writer's caller does: the balance trigger fires at
COMMIT, and a rejection has to surface while the caller can still react.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from finance.api.writers import _insert_transfer_match
from finance.domain.models.accounts import Account
from finance.domain.models.importer import SourceRecord
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.models.transfers import (
    TransferReview,
    TransferSkip,
)
from finance.domain.services.transfer_match import (
    JournalLineRef,
    TransferPair,
    transfer_match,
    transfer_window,
)

__all__ = [
    "TransferLinkReport",
    "confirm_review",
    "ignore_review",
    "link_transfers",
    "reject_review",
]


@dataclass(frozen=True)
class TransferLinkReport:
    """What one sweep over the unmatched legs decided."""

    outbounds_examined: int
    auto_matched: int
    review_queued_multi: int
    review_queued_low_confidence: int
    already_matched: int
    skipped_by_user: int


def _outbounds(
    session: Session,
    *,
    batch_id: int | None,
    from_: dt.date | None,
    to_: dt.date | None,
) -> list[JournalLine]:
    """Every unmatched outbound leg in scope, in id order.

    Outbound means `amount < 0` in the line's own currency on an
    asset/liability account. Equity is excluded — its lines are the
    contra-legs of manual expenses, not money moving between real accounts —
    and so is the system expense account, for the same reason. Synthesized
    legs are excluded: a synthesized leg is the side no statement printed,
    owned by the card-payment writer (`finance.api.writers`), which matches
    it by exact amount within its own window when the other statement
    arrives. The generic sweep must not link a placeholder to a stranger —
    or to its own entry's real leg — before the other side arrives. When
    `batch_id` is given, only lines on entries that batch points at are in
    scope, expressed as an `IN (subquery)` so a card-payment entry with more
    than one `source_record` cannot duplicate its lines the way a join would.
    """
    stmt = (
        select(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalLine.transfer_match_id.is_(None))
        .where(JournalLine.is_synthesized.is_(False))
        .where(Account.account_nature.in_(["asset", "liability"]))
        .where(
            or_(
                Account.system_role.is_(None),
                Account.system_role != "system_expense",
            )
        )
        .where(JournalLine.amount < 0)
        .order_by(JournalLine.id)
    )
    if batch_id is not None:
        owned = select(SourceRecord.journal_entry_id).where(
            SourceRecord.import_batch_id == batch_id,
            SourceRecord.journal_entry_id.is_not(None),
        )
        stmt = stmt.where(JournalLine.journal_entry_id.in_(owned))
    if from_ is not None:
        stmt = stmt.where(JournalEntry.entry_date >= from_)
    if to_ is not None:
        stmt = stmt.where(JournalEntry.entry_date <= to_)
    return list(session.scalars(stmt))


def _candidates(
    session: Session, *, outbound: JournalLine, outbound_date: dt.date
) -> list[JournalLine]:
    """Every line that could be this outbound's incoming half, in id order.

    Unmatched, real (never synthesized — see `_outbounds`), on a DIFFERENT
    asset/liability account and a DIFFERENT entry: two legs of one entry are
    already one event, and linking them would stamp a `transfer_match` on an
    entry that balances by itself. Positive amount, in the outbound's window —
    and never a pair the user told the sweep to stop suggesting
    (`transfer_skip`).
    """
    earliest, latest = transfer_window(outbound_date)
    skipped = select(TransferSkip.inbound_journal_line_id).where(
        TransferSkip.outbound_journal_line_id == outbound.id
    )
    stmt = (
        select(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalLine.transfer_match_id.is_(None))
        .where(JournalLine.is_synthesized.is_(False))
        .where(JournalLine.account_id != outbound.account_id)
        .where(JournalLine.journal_entry_id != outbound.journal_entry_id)
        .where(Account.account_nature.in_(["asset", "liability"]))
        .where(
            or_(
                Account.system_role.is_(None),
                Account.system_role != "system_expense",
            )
        )
        .where(JournalLine.amount > 0)
        .where(JournalEntry.entry_date >= earliest)
        .where(JournalEntry.entry_date <= latest)
        .where(~JournalLine.id.in_(skipped))
        .order_by(JournalLine.id)
    )
    return list(session.scalars(stmt))


def _entry_date(session: Session, line: JournalLine) -> dt.date:
    """The entry date of a line's entry. Entries never move without lines."""
    entry = session.get(JournalEntry, line.journal_entry_id)
    if entry is None:  # pragma: no cover - the line's FK guarantees it
        raise ValueError(f"journal_entry {line.journal_entry_id} is missing")
    return entry.entry_date


def _mark_transfer(session: Session, *, first_id: int, second_id: int) -> None:
    """Flag both lines' entries as transfers. A linked pair is one event."""
    for line_id in (first_id, second_id):
        line = session.get(JournalLine, line_id)
        if line is None:  # pragma: no cover - ids came from this session
            raise ValueError(f"journal_line {line_id} vanished mid-match")
        entry = session.get(JournalEntry, line.journal_entry_id)
        if entry is None:  # pragma: no cover - the line's FK guarantees it
            raise ValueError(f"journal_entry {line.journal_entry_id} is missing")
        entry.is_transfer = True


def _has_pending_review(session: Session, *, outbound_id: int) -> bool:
    """Whether a pending review already queues this outbound leg."""
    return (
        session.scalar(
            select(TransferReview.id)
            .where(TransferReview.outbound_journal_line_id == outbound_id)
            .where(TransferReview.status == "pending")
            .limit(1)
        )
        is not None
    )


def _has_skip(session: Session, *, outbound_id: int) -> bool:
    """Whether the user has skipped any pair for this outbound leg."""
    return (
        session.scalar(
            select(TransferSkip.id)
            .where(TransferSkip.outbound_journal_line_id == outbound_id)
            .limit(1)
        )
        is not None
    )


def link_transfers(
    session: Session,
    *,
    batch_id: int | None = None,
    from_: dt.date | None = None,
    to_: dt.date | None = None,
) -> TransferLinkReport:
    """Link unmatched outbound legs to their incoming halves.

    For each outbound leg, the pure `transfer_match` rule is run against
    every candidate, and the outcome branches exactly one way:

    * no match: nothing.
    * exactly one match that `is_auto` in the SAME currency (the only such
      shape is same-currency exact same-day at 0.95): auto-link with
      `match_method="auto_amount_date"`, `confidence=0.95`, unconfirmed.
    * exactly one weaker match (same-currency within tolerance but not
      exact, or any cross-currency pair): queue a `low_confidence` review.
    * two or more matches: queue a `multi_candidate` review. NEVER auto-link.

    Cross-currency NEVER auto-links even when the pure rule's tolerance would
    allow the pair — a lone cross-currency candidate is `low_confidence` and
    multiples are `multi_candidate`.

    Idempotent: a pending review for the outbound blocks a second one (the
    partial unique index would otherwise raise on a re-run), and a re-run
    over an already-linked ledger writes nothing new. Never begins, commits,
    or rolls back; the caller owns the transaction.
    """
    outbounds = _outbounds(session, batch_id=batch_id, from_=from_, to_=to_)
    auto_matched = 0
    review_queued_multi = 0
    review_queued_low_confidence = 0
    already_matched = 0
    skipped_by_user = 0

    for outbound in outbounds:
        if outbound.transfer_match_id is not None:
            # Defensive: the query already filters these, so this only fires
            # when the line was linked after it was selected.
            already_matched += 1
            continue
        if _has_pending_review(session, outbound_id=outbound.id):
            # The question is already in the user's queue; asking it again
            # would hit the partial unique index instead of helping.
            skipped_by_user += 1
            continue
        outbound_date = _entry_date(session, outbound)
        out_ref = JournalLineRef(
            journal_line_id=outbound.id,
            account_id=outbound.account_id,
            amount_minor=outbound.amount,
            currency=outbound.currency.strip(),
            booked_date=outbound_date,
            already_matched=False,
        )
        matched: list[tuple[TransferPair, JournalLine]] = []
        for candidate in _candidates(
            session, outbound=outbound, outbound_date=outbound_date
        ):
            cand_ref = JournalLineRef(
                journal_line_id=candidate.id,
                account_id=candidate.account_id,
                amount_minor=candidate.amount,
                currency=candidate.currency.strip(),
                booked_date=_entry_date(session, candidate),
                already_matched=False,
            )
            pair = transfer_match(out_ref, cand_ref)
            if pair is not None:
                matched.append((pair, candidate))
        if not matched:
            if _has_skip(session, outbound_id=outbound.id):
                # Every plausible pair was one the user dismissed; that is a
                # skip the user made, not a leg with no counterpart.
                skipped_by_user += 1
            continue
        if len(matched) == 1:
            pair, candidate = matched[0]
            same_currency = outbound.currency.strip() == candidate.currency.strip()
            if pair.is_auto and same_currency:
                _insert_transfer_match(
                    session,
                    out_line_id=outbound.id,
                    in_line_id=candidate.id,
                    match_method="auto_amount_date",
                    confidence=Decimal("0.95"),
                    confirmed=False,
                )
                _mark_transfer(session, first_id=outbound.id, second_id=candidate.id)
                auto_matched += 1
                continue
            session.add(
                TransferReview(
                    outbound_journal_line_id=outbound.id,
                    candidate_journal_line_ids=[candidate.id],
                    reason="low_confidence",
                )
            )
            session.flush()
            review_queued_low_confidence += 1
            continue
        session.add(
            TransferReview(
                outbound_journal_line_id=outbound.id,
                candidate_journal_line_ids=[candidate.id for _, candidate in matched],
                reason="multi_candidate",
            )
        )
        session.flush()
        review_queued_multi += 1

    return TransferLinkReport(
        outbounds_examined=len(outbounds),
        auto_matched=auto_matched,
        review_queued_multi=review_queued_multi,
        review_queued_low_confidence=review_queued_low_confidence,
        already_matched=already_matched,
        skipped_by_user=skipped_by_user,
    )


def _pending_review(session: Session, *, review_id: int) -> TransferReview:
    """The pending review, or why there is nothing to decide."""
    review = session.get(TransferReview, review_id)
    if review is None:
        raise ValueError(f"No transfer_review {review_id}")
    if review.status != "pending":
        raise ValueError(
            f"transfer_review {review_id} is {review.status!r}, not pending"
        )
    return review


def confirm_review(
    session: Session, *, review_id: int, candidate_journal_line_id: int | None = None
) -> int:
    """Confirm a pending review, linking the outbound to its counterparty.

    A review with more than one candidate requires `candidate_journal_line_id`
    naming one of them; a review with exactly one uses it. The link is stored
    via `_insert_transfer_match` with `match_method="user_confirmed"`,
    `confidence=1.00`, confirmed — a human agreed, which is a different claim
    from the matcher deciding. Both entries are flagged `is_transfer`, the
    review is marked `confirmed`, and the new `transfer_match` id is returned.
    """
    review = _pending_review(session, review_id=review_id)
    candidates = list(review.candidate_journal_line_ids)
    if len(candidates) > 1:
        if candidate_journal_line_id is None:
            raise ValueError(
                f"transfer_review {review_id} has {len(candidates)} "
                "candidates; pass candidate_journal_line_id"
            )
        if candidate_journal_line_id not in candidates:
            raise ValueError(
                f"journal_line {candidate_journal_line_id} is not a "
                f"candidate on transfer_review {review_id}"
            )
        chosen = candidate_journal_line_id
    elif len(candidates) == 1:
        sole = candidates[0]
        if candidate_journal_line_id is not None and (
            candidate_journal_line_id != sole
        ):
            raise ValueError(
                f"journal_line {candidate_journal_line_id} is not a "
                f"candidate on transfer_review {review_id}"
            )
        chosen = sole
    else:
        raise ValueError(f"transfer_review {review_id} has no candidates")
    outbound = session.get(JournalLine, review.outbound_journal_line_id)
    if outbound is None:
        raise ValueError(f"journal_line {review.outbound_journal_line_id} is missing")
    inbound = session.get(JournalLine, chosen)
    if inbound is None:
        raise ValueError(f"journal_line {chosen} is missing")
    match_id = _insert_transfer_match(
        session,
        out_line_id=outbound.id,
        in_line_id=inbound.id,
        match_method="user_confirmed",
        confidence=Decimal("1.00"),
        confirmed=True,
    )
    _mark_transfer(session, first_id=outbound.id, second_id=inbound.id)
    review.status = "confirmed"
    review.resolved_at = func.now()
    session.flush()
    return match_id


def reject_review(session: Session, *, review_id: int) -> None:
    """Reject a pending review. No match, lines untouched, entry flags kept.

    The pair was not a transfer after all: both lines stay unmatched (and
    their entries keep whatever `is_transfer` they had), and only the review
    moves, to `rejected`.
    """
    review = _pending_review(session, review_id=review_id)
    review.status = "rejected"
    review.resolved_at = func.now()
    session.flush()


def ignore_review(session: Session, *, review_id: int) -> None:
    """Ignore a pending review, silencing every candidate pair it named.

    One `TransferSkip` row per candidate, so the sweep stops re-suggesting
    pairs the user has seen and dismissed. Existing skip rows are kept, not
    duplicated. The lines stay unmatched, like a rejection.
    """
    review = _pending_review(session, review_id=review_id)
    review.status = "ignored"
    review.resolved_at = func.now()
    existing = set(
        session.scalars(
            select(TransferSkip.inbound_journal_line_id).where(
                TransferSkip.outbound_journal_line_id
                == review.outbound_journal_line_id,
                TransferSkip.inbound_journal_line_id.in_(
                    list(review.candidate_journal_line_ids)
                ),
            )
        )
    )
    for candidate_id in review.candidate_journal_line_ids:
        if int(candidate_id) in existing:
            continue
        session.add(
            TransferSkip(
                outbound_journal_line_id=review.outbound_journal_line_id,
                inbound_journal_line_id=int(candidate_id),
            )
        )
    session.flush()
