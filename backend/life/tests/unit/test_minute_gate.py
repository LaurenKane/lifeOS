"""test_minute_gate.py — the tripwire counts, late hits fall out.

Injectable clock, no sleeps: under cap passes, over cap raises, and only the
same key's hits count against each other. This also covers the negated case
of the route test — the over-cap 429 is proven here on a bare gate, never
against the real app route.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from life.api.routes.calendar import MinuteGate


class FakeClock:
    """Seconds, in the caller's hands."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def a_gate(cap: int = 2) -> tuple[MinuteGate, FakeClock]:
    clock = FakeClock()
    return MinuteGate(cap=cap, clock=clock), clock


class TestMinuteGate:
    def test_under_cap_passes(self) -> None:
        gate, clock = a_gate(cap=3)
        gate.check("phone")
        clock.advance(1.0)
        gate.check("phone")
        clock.advance(1.0)
        gate.check("phone")

    def test_over_cap_raises_429(self) -> None:
        gate, clock = a_gate(cap=2)
        gate.check("phone")
        clock.advance(0.5)
        gate.check("phone")
        clock.advance(0.5)
        with pytest.raises(HTTPException) as raised:
            gate.check("phone")
        assert raised.value.status_code == 429

    def test_only_the_same_key_counts(self) -> None:
        """One chatty host never trips another's window: phone is at cap,
        and laptop's first call still passes at the same moment."""
        gate, clock = a_gate(cap=1)
        gate.check("phone")
        clock.advance(0.5)
        with pytest.raises(HTTPException) as raised:
            gate.check("phone")
        assert raised.value.status_code == 429
        gate.check("laptop")

    def test_hits_die_out_of_the_window(self) -> None:
        """Sliding minute: after a minute of silence the key may call again."""
        gate, clock = a_gate(cap=1)
        gate.check("phone")
        clock.advance(60.1)
        gate.check("phone")
