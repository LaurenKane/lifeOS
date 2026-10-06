"""sweep.py — the nightly transfer-matching sweep over unmatched legs.

A thin wrapper over `finance.api.transfer_linker.link_transfers`: the same
sweep, defaulting to the 90 days back through today. An explicit `from_`/`to_`
overrides the default; the linker owns every other decision.

There is no worker container (PHASE2-PLAN §3c): host cron runs
`lifeos-cli sweep`, which calls this with the default window.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from finance.api.transfer_linker import TransferLinkReport, link_transfers

__all__ = ["sweep_unmatched"]


def sweep_unmatched(
    session: Session,
    *,
    from_: dt.date | None = None,
    to_: dt.date | None = None,
) -> TransferLinkReport:
    """Link every unmatched outbound leg in the window. The caller owns it.

    Args:
        session: The caller's session. Never begun, committed or rolled back
            here — like every writer, this only writes inside the caller's
            transaction.
        from_: The first entry date in scope, or None for 90 days back.
        to_: The last entry date in scope, or None for today.

    Returns:
        What the sweep decided, per `TransferLinkReport`.
    """
    today = dt.date.today()
    start = from_ if from_ is not None else today - dt.timedelta(days=90)
    end = to_ if to_ is not None else today
    return link_transfers(session, batch_id=None, from_=start, to_=end)
