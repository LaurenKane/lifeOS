"""organize — the helper's CONTRACT, no HTTP.

The inbox helper is provider-agnostic: one OpenAI-compatible chat-completions
call, suggestions only, nothing applied server-side. This module holds the
two ends of that call that touch no network:

- `build_prompt`: the exact prompt text sent to the helper. It speaks the
  user's OWN module vocabulary (Thought → Action / Upkeep / rest / dismiss)
  and obeys vocabulary law verbatim: a due date may be suggested only when
  the Thought's text itself says when, relative to `today_local`; "urgent"
  only when the text's own words make it so.
- `parse_suggestions`: the boundary that makes the helper's answer safe.
  The model may hallucinate a thought_id, an off-vocabulary choice, or a
  date in the unreachable past — every such entry is silently DROPPED, and
  nothing is invented: the parser proposes only what the model returned.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Sequence, cast

__all__ = ["Suggestion", "ThoughtFact", "build_prompt", "parse_suggestions"]

#: The four choices the helper may speak — the user's own words, exactly.
#: The resolve endpoint's server verbs ("rests", "dismissed") are a router
#: concern; this contract uses the pile's own vocabulary.
_ALLOWED_CHOICES: Final[frozenset[str]] = frozenset(
    {"action", "upkeep", "keep", "dismiss"}
)

#: The longest `why` allowed through the boundary, truncated here (not by
#: the frontend) so every consumer sees the same deterministic cut.
_MAX_WHY: Final[int] = 120


@dataclass(frozen=True)
class ThoughtFact:
    """One unresolved Thought as the prompt sees it: id, words, age."""

    thought_id: int
    text: str
    created_at: dt.datetime


@dataclass(frozen=True)
class Suggestion:
    """One suggestion to show the user. NEVER applied silently — the
    response is preview rows, and the user approves each one."""

    thought_id: int
    choice: str
    due_date: dt.date | None = None
    urgent: bool = False
    why: str = ""


def build_prompt(thoughts: Sequence[ThoughtFact], *, today_local: dt.date) -> str:
    """The full prompt text: instructions, today, then the Thought list.

    The rules are written for the model to be able to follow them: dates
    must come from the Thought's own words, never invented; a Thought with
    no useful suggestion is skipped rather than forced.
    """
    listed = "\n".join(f'- id {t.thought_id}: "{t.text}"' for t in thoughts)
    return (
        "You help a person sort thoughts they captured in a personal tracker. "
        "Propose, for each Thought below, the ONE thing it most likely is. "
        "Answer with ONLY a JSON array — no prose before or after.\n"
        "\n"
        "Each choice is one of the user's own vocabulary:\n"
        '- "action": the Thought names something concrete to do once — it '
        "becomes an Action. Give a due_date ONLY when the Thought's own text "
        'says when ("tomorrow", "on the 20th", a weekday name), derived '
        "relative to today. Never invent a date the text does not contain, "
        "and never guess a future date the person meant nothing fixed by. "
        "Set urgent true only when the text's own words make it urgent.\n"
        '- "upkeep": the Thought describes a recurring upkeep worth a receipt.\n'
        '- "keep": it stays a Thought, left where it is.\n'
        '- "dismiss": it is not important enough to keep; the pile is '
        "resolved on it.\n"
        "\n"
        'Every entry: {"thought_id": <the id from the list>, "choice": '
        '"action" | "upkeep" | "keep" | "dismiss", "due_date": "YYYY-MM-DD" '
        'or "", "urgent": true or false, "why": "<= 12 short words, a fact, '
        'not a judgment"}\n'
        "Skip a Thought you have nothing useful to say about — do not force "
        "an entry for every id.\n"
        "\n"
        f"Today is {today_local.isoformat()} (the user's local day).\n"
        "The Thoughts:\n"
        f"{listed}\n"
    )


def _first_json_array(raw: str) -> list[object] | None:
    """The first JSON array in `raw`, robust to `` `-fences and prose.

    Returns None when no parseable array exists — an unusable answer is
    "no suggestions", never an exception: the pile is untouched either way.
    """
    text = raw.strip()
    if text.startswith("```"):
        # A fenced block: the first line carries the fence (often a language
        # tag), the last line closes it. Strip both, then parse what is left.
        lines = text.splitlines()
        body = lines[1:-1] if len(lines) > 2 else lines[1:]
        text = "\n".join(body).strip()

    decoder = json.JSONDecoder()
    index = text.find("[")
    while index != -1:
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            index = text.find("[", index + 1)
            continue
        if isinstance(value, list):
            return value
        index = text.find("[", index + 1)
    return None


def _clean_due_date(raw: object, *, today_local: dt.date) -> dt.date | None:
    """The suggested date, only if parseable and not before today.

    A date behind the calendar is the model over-reaching; drop it and let
    the entry stand without a date rather than surfacing a day that is gone.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
    return parsed if parsed >= today_local else None


def _clean_urgent(raw: object) -> bool:
    """Urgent only when the model said it plainly — not via truthy junk."""
    return raw is True or (isinstance(raw, str) and raw.strip().lower() == "true")


def parse_suggestions(
    raw: str, thoughts: Sequence[ThoughtFact], *, today_local: dt.date
) -> list[Suggestion]:
    """The model's answer → valid Suggestions only.

    Every entry is checked against the thought_ids actually sent; an unknown
    id, a malformed id, a choice outside the four, or a non-object entry is
    dropped. `why` is truncated to 120 characters here, deterministically.
    An answer with nothing valid parses to [] — no suggestion is invented.
    """
    entries = _first_json_array(raw)
    if entries is None:
        return []

    known = {fact.thought_id for fact in thoughts}
    suggestions: list[Suggestion] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        values: Mapping[str, object] = cast("dict[str, object]", entry)
        raw_id = values.get("thought_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            continue
        if raw_id not in known:
            continue
        raw_choice = values.get("choice")
        choice = raw_choice.strip().lower() if isinstance(raw_choice, str) else ""
        if choice not in _ALLOWED_CHOICES:
            continue
        raw_why = values.get("why")
        why = raw_why[:_MAX_WHY] if isinstance(raw_why, str) else ""
        suggestions.append(
            Suggestion(
                thought_id=raw_id,
                choice=choice,
                due_date=_clean_due_date(
                    entry.get("due_date"), today_local=today_local
                ),
                urgent=_clean_urgent(entry.get("urgent")),
                why=why,
            )
        )
    return suggestions
