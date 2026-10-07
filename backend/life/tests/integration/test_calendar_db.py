"""test_calendar_db.py — the fenced feed, end to end and against real migrations.

What is certain: a dated open Action appears on the wire, a done one does
not; the reply is `text/calendar`; an empty token answers 404 (the feed is
OFF, not locked-and-waiting) and a wrong token answers the SAME 404. The
token only ever lives in a monkeypatched environment (SAFETY.md rule 3). The
over-cap 429 is deliberately NOT exercised against the real route — that is
the unit test's job.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from config import get_settings
from fastapi.testclient import TestClient
from main import create_app

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


class TestFeed:
    def test_a_dated_open_action_reaches_the_wire(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LIFEOS_ICS_TOKEN", "test-token-3f")
        get_settings.cache_clear()
        try:
            created = client.post(
                f"{PREFIX}/actions",
                json={
                    "text": "renew the bike care subscription",
                    "due_date": "2026-10-14",
                },
            )
            assert created.status_code == 201
            action_id: int = created.json()["id"]

            got = client.get(f"{PREFIX}/calendar/test-token-3f")
            assert got.status_code == 200
            assert got.headers["content-type"] == "text/calendar; charset=utf-8"
            assert "private" in got.headers["cache-control"]
            assert "no-store" in got.headers["cache-control"]
            body = got.text
            assert "X-WR-CALNAME:LifeOS" in body
            assert "renew the bike care subscription" in body
            assert f"UID:feed-action-{action_id}@lifeos" in body
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_a_done_action_rests_off_the_calendar(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LIFEOS_ICS_TOKEN", "test-token-3f")
        get_settings.cache_clear()
        try:
            created = client.post(
                f"{PREFIX}/actions",
                json={"text": "put the desk together", "due_date": "2026-10-10"},
            )
            assert created.status_code == 201
            marked = client.post(f"{PREFIX}/actions/{created.json()['id']}/done")
            assert marked.status_code == 200
            still_open = client.post(
                f"{PREFIX}/actions",
                json={
                    "text": "renew the bike care subscription",
                    "due_date": "2026-10-14",
                },
            )
            assert still_open.status_code == 201

            got = client.get(f"{PREFIX}/calendar/test-token-3f")
            assert got.status_code == 200
            assert "put the desk together" not in got.text
            assert "renew the bike care subscription" in got.text
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    def test_an_empty_token_is_a_feed_that_is_off(self, client: TestClient) -> None:
        """Unset means OFF: 404, never a locked-and-waiting other answer."""
        got = client.get(f"{PREFIX}/calendar/anything")
        assert got.status_code == 404

    def test_a_wrong_token_says_the_same_as_off(self, client: TestClient) -> None:
        """One 404 for off and wrong: no oracle for telling them apart."""
        got = client.get(f"{PREFIX}/calendar/wrong-guess")
        assert got.status_code == 404
