"""The daily digest — the day's own board, recomposed as one push message.

Pure composition: no HTTP, no database, no clock. `gather` (life.api.digest)
builds the facts from the same helpers the today route uses; this module only
decides how those facts are WORDS.

The composition rule is why the shape looks the way it does: a push carries
FACTS the user would already see on their Dashboard — never manufactured
urgency, never a judgment (vocabulary law, ADR 0011: no "overdue", no
"streak", no "habit" concept exists to report). A day with nothing on it is
not padded with filler or stats either: it speaks exactly the same words the
dashboard says when the board is empty — "Nothing assigned to today —
good." — so the push and the screen never disagree about what an empty day
means.

Sections follow the board's own fixed order (do-now, urgent, upkeep
opportunities, upcoming), blank lines between. The summary line counts
top-level entries ("1 thing on today", "3 things on today") and appends "· N
kept back" when the cap cut something — the same honest kept-back count the
Dashboard shows.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

__all__ = ["DigestFacts", "DigestMessage", "UpcomingFact", "compose_digest"]

#: The empty day's line — verbatim, because the Dashboard's empty state
#: (frontend/src/features/life/dashboard/page.tsx) already says exactly this
#: and a second wording would be two words for one fact.
#: A literal dash-free alternative: compose with `_EMPTY_DAY`.
_EMPTY_DAY_LINE: str = "Nothing assigned to today — good."

#: Per-item section prefixes, in the list order the message uses.
_DO_NOW_BULLET: str = "- "
_URGENT_PREFIX: str = "urgent: "
_UPKEEP_PREFIX: str = "upkeep open: "
_UPCOMING_PREFIX: str = "upcoming: "


@dataclass(frozen=True)
class UpcomingFact:
    """A dated Action inside the route's upcoming horizon, display-ready."""

    #: The action's own text. Quoted in the composed line.
    text: str
    #: The real world's due date — a fact, then rendered as one line.
    due_date: dt.date


@dataclass(frozen=True)
class DigestFacts:
    """What the day's board holds, as display-ready strings.

    The strings are composed by the caller (which owns the display rules);
    this dataclass only guarantees the sections are named the same way the
    board names them.
    """

    #: Top-level do-now entries, board-ordered.
    do_now: list[str]
    #: Urgent open top-level actions (the user's own flag, its own block).
    urgent: list[str]
    #: Eligible entries cut by the cap — a count, never their names.
    kept_back: int
    #: Upkeeps whose window is open today, aim already baked in.
    upkeep_opportunities: list[str]
    #: Dated upcoming actions inside the route's horizon, one per entry.
    upcoming: list[UpcomingFact]


@dataclass(frozen=True)
class DigestMessage:
    """One push notification: a title and a body. Nothing else."""

    title: str
    body: str


def _summary_line(facts: DigestFacts) -> str:
    """The count line: N top-level things on today, plus kept-back when cut."""
    count = len(facts.do_now)
    line = f"{count} thing on today" if count == 1 else f"{count} things on today"
    if facts.kept_back >= 1:
        line = f"{line} · {facts.kept_back} kept back"
    return line


def compose_digest(facts: DigestFacts, *, today_local: dt.date) -> DigestMessage:
    """Turn the day's facts into the one pushed message.

    Args:
        facts: What the board holds, composed by the caller.
        today_local: The user's local day — the same day the facts were
            gathered for; it is in the title only.

    Returns:
        The message to push. An empty day composes the dashboard's own
        empty-state line and nothing else.
    """
    title = f"LifeOS · {today_local:%a %-d %b}"

    sections: list[list[str]] = []
    if facts.do_now:
        line = _summary_line(facts)
        sections.append([line, "", *(_DO_NOW_BULLET + text for text in facts.do_now)])
    if facts.urgent:
        sections.append([_URGENT_PREFIX + text for text in facts.urgent])
    if facts.upkeep_opportunities:
        sections.append([_UPKEEP_PREFIX + text for text in facts.upkeep_opportunities])
    if facts.upcoming:
        sections.append(
            [
                _UPCOMING_PREFIX + f'"{fact.text}" on {fact.due_date:%a %-d %b}'
                for fact in facts.upcoming
            ]
        )

    body = "\n\n".join("\n".join(section) for section in sections)
    if not body:
        body = _EMPTY_DAY_LINE
    return DigestMessage(title=title, body=body)
