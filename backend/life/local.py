"""Where 'today' and 'now' mean the user's day, not the server clock's.

The life module's read-side ("today's board", "missed by ~2 days") is
timezone-sensitive in a way the ledger is not: a transaction booked at
23:40 UTC belongs to one date forever, but "today" must be Amsterdam's
today. Settings field: `LOCAL_TIMEZONE` (config.py), default
Europe/Amsterdam.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from config import get_settings

__all__ = ["local_now", "local_today"]


def _zone() -> ZoneInfo:
    """The configured local zone, as a ZoneInfo."""
    return ZoneInfo(get_settings().LOCAL_TIMEZONE)


def local_now() -> dt.datetime:
    """Now in the configured local timezone (aware)."""
    return dt.datetime.now(_zone())


def local_today() -> dt.date:
    """Today in the configured local timezone."""
    return local_now().date()
