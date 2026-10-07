"""Upkeep matching — does a capture name an existing Upkeep?

Discovery rules this file implements, verbatim from the interviews:

- **Q21b:** a receipt is recorded from a capture only when the parser matched
  ONE named Upkeep with high confidence. A loose phrase ("did some cleaning")
  must not silently write a fact into the cadence history — that history is
  the source of the earned cadence, and a poisoned history is unrecoverable
  from the UI.
- **Q20:** when a capture matches an existing Upkeep but does not read as a
  completion ("bathroom" vs "cleaned the bathroom"), it asks ONE question:
  receipt, or new action? The match comes back `confident=False` and the
  client asks.

Pure functions: the caller hands in the (id, title) pairs, this module knows
nothing about sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Sequence

__all__ = ["UpkeepMatch", "match_upkeep"]


@dataclass(frozen=True)
class UpkeepMatch:
    """An upkeep named by a captured line. `confident` decides the flow:

    True  → the capture, minus receipt verbs and date words, says nothing
            beyond the upkeep name: a receipt, echoed with undo (Q21b).
    False → an upkeep is in play but intent is unclear: the client asks
            receipt-or-action (Q20).
    """

    upkeep_id: int
    title: str
    confident: bool


#: Words that turn a noun phrase into a completion statement ("cleaned the
#: bathroom", "did laundry", "changed the bedding"). PAST tense only: an
#: imperative ("clean the bathroom") must read as an intent, not a fact —
#: a wrongly recorded receipt poisons the earned cadence, which is
#: unrecoverable from the UI. The set is deliberately small and closed:
#: matching MORE verbs makes receipts lie.
RECEIPT_VERBS: Final[frozenset[str]] = frozenset(
    {
        "did",
        "done",
        "cleaned",
        "washed",
        "finished",
        "swept",
        "vacuumed",
        "tidied",
        "emptied",
        "changed",
        "replaced",
        "watered",
        "fed",
        "binned",
        "mowed",
        "ironed",
        "folded",
        "took",
    }
)

#: Articles/possessives that add no meaning between a verb and the noun.
_FILLER_WORDS: Final[frozenset[str]] = frozenset(
    {"the", "a", "an", "my", "our", "its", "it", "out", "up", "some", "all"}
)

_NON_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9 ]+")


def _stem(word: str) -> str:
    """A minimal English verb stem: -ed / -ing / -es suffixes fall off when the
    remaining word is still substantial. "cleaned"→"clean", "washed"→"wash".
    Deliberately conservative (irregular "did"/"took" pass through unchanged):
    a wrong stem inside a cadence record is worse than a missed alias."""
    if len(word) <= 4:
        return word
    for suffix in ("ing", "ed", "es"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _normalize(text: str) -> str:
    """Lower-cased, punctuation-removed, single-spaced words."""
    return " ".join(_NON_WORD_PATTERN.sub(" ", text.lower()).split())


def _stems(words: list[str]) -> set[str]:
    return {_stem(word) for word in words}


def _word_groups(text: str) -> tuple[int, ...]:
    """The word positions of the Nth word — order-independence helper."""
    return tuple(range(len(text.split())))


def match_upkeep(
    text: str,
    candidates: Sequence[tuple[int, str]],
) -> UpkeepMatch | None:
    """Match one captured line against the named Upkeeps.

    Args:
        text: The capture text, AFTER date/urgent words were removed by
            `parse_capture` — the caller passes `ParsedCapture.text`.
        candidates: `(id, title)` pairs of every live Upkeep.

    Returns:
        A match, or None when no single upkeep is clearly in play. Multiple
        candidate matches return None as well: an ambiguous ask is worse than
        a plain Thought (the capture didn't block; a Thought always stands).
    """
    norm_text = _normalize(text)
    if not norm_text:
        return None

    text_words = norm_text.split()
    text_stems = _stems(text_words)
    matches: list[tuple[int, str]] = []
    for upkeep_id, title in candidates:
        norm_title = _normalize(title)
        title_words = norm_title.split()
        title_stems = _stems(title_words)
        # Stemmed containment: "cleaned the bathroom" names a "clean
        # bathroom"-titled upkeep even though the words conjugate.
        title_in_capture = all(stem in text_stems for stem in title_stems)
        # Reversed: the capture is a SUBSET of the title ("bathroom" against a
        # 'clean bathroom' upkeep) — an intent ask, never a confident receipt.
        capture_in_title = (
            len(text_words) >= 1
            and all(_stem(word) in title_stems for word in text_words)
            and any(len(word) >= 4 for word in text_words)
        )
        if title_in_capture or capture_in_title:
            matches.append((upkeep_id, title))

    if len(matches) == 1:
        upkeep_id, title = matches[0]
        title_stems = _stems(_normalize(title).split())
        leftover = [word for word in text_words if _stem(word) not in title_stems]
        # Confident = there IS a completion statement (at least one word
        # beyond the name — a bare "bathroom" is a reminder intent, Q20) and
        # every leftover word is a PAST-TENSE receipt verb (stemming is for
        # titles only: "clean bathroom" is an imperative, "cleaned the
        # bathroom" is a fact) or a filler. Any unknown word breaks
        # confidence: quiet conservatism beats a lying receipt.
        confident = bool(leftover) and all(
            word in RECEIPT_VERBS or word in _FILLER_WORDS for word in leftover
        )
        return UpkeepMatch(upkeep_id=upkeep_id, title=title, confident=confident)
    return None
