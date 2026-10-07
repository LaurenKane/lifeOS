"""pixels router — the activity grid's data.

One day, one count: completed Actions plus Upkeep receipts, unioned. No fill
levels, no streaks — the rule (Q25) is `count >= 1` renders, everything else
renders faint, and no red exists anywhere in the schema to support a red.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, cast

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.local import local_now
from life.public import PixelDay

router = APIRouter(tags=["life"], prefix="/life/pixels")

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[PixelDay], summary="Days you did something")
def pixels(
    session: SessionDep,
    days: int = 371,
) -> list[PixelDay]:
    """One row per day that has activity, in the requested window.

    Days WITHOUT activity are absent from the response by design: the grid
    renders the window itself and fills what the facts mark, so an empty day
    is an honest absence — there is no schema state that could call one red.
    """
    start = local_now() - dt.timedelta(days=days)
    rows = session.execute(
        text(
            "SELECT t.day AS day, count(*) AS count FROM ("
            "  SELECT (a.done_at)::date AS day FROM action a"
            "   WHERE a.is_done AND a.done_at >= :start"
            "  UNION ALL"
            "  SELECT r.receipted_at::date AS day FROM receipt r"
            "   WHERE r.receipted_at >= :start"
            ") t GROUP BY t.day ORDER BY t.day"
        ),
        {"start": start},
    ).all()
    return [
        PixelDay(date=cast("dt.date", row[0]), count=cast("int", row[1]))
        for row in rows
    ]
