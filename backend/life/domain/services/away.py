"""away_days — whole days of silence, the only number the away strip reads.

Pure function; the gatherer passes in the local now (glossary-adjacent rule:
"away" is measured in the user's days, never the server's UTC arm).

Why `last_activity` is max over thought.created_at, done-action done_at,
receipt.receipted_at: capturing IS showing up (putting a line into the pile
is attention, even before any sorting); doing is showing up (a receipt or a
done Action is the thing itself). Only silence — none of the three for more
than ~2 days — is the trigger for the strip (Q8). The WHY lives in code so a
future read source (pixels count another way) has to answer it explicitly.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["away_days"]


def away_days(last_activity: dt.datetime | None, *, now_local: dt.datetime) -> int:
    """Whole days since the last moment of activity; 0 when there is none
    (`None`) or the gap is under a day.

    Whole days and nothing finer: "away 1 day" starts at the first midnight
    past the activity, and a same-moment comparison is 0, never -1 — a
    clock skews backwards as historiography, not as accusation.
    """
    if last_activity is None:
        return 0
    days = (now_local - last_activity).days
    return days if days >= 1 else 0
