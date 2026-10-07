"""test_catchup_db.py — the away flow, end to end, on the real migrations.

The strip's claims, in HTTP terms: whole days from the three activity
sources, pending = active goals + unresolved thoughts, one ack per day
(idempotent), and clearing is a drop-out of the strip only — a dismissed
Thought is KEPT, a paused Goal is resting, both visible on their own pages.
No judgment words anywhere in the contract (vocabulary law).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

from life.local import local_now, local_today

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


class TestAwayDaysRead:
    def test_recent_activity_reads_zero_away_days(self, client: TestClient) -> None:
        """A capture IS showing up: the strip stays quiet on the number."""
        capture = client.post(f"{PREFIX}/capture", json={"text": "tidy the desk"})
        assert capture.status_code == 201

        body = client.get(f"{PREFIX}/catchup").json()
        assert body["away_days"] == 0

    def test_an_old_receipt_is_the_away_measurement(self, client: TestClient) -> None:
        """Only silence accumulates days: the last done moment is five days
        behind, so away_days says five-ish — whole days, floored."""
        title = "clean bathroom"
        upkeeps = client.get(f"{PREFIX}/upkeeps").json()
        upkeep = next((u for u in upkeeps if u["title"] == title), None)
        if upkeep is None:
            client.post(f"{PREFIX}/upkeeps", json={"title": title, "aim_days": 7})
            upkeep = [
                u for u in client.get(f"{PREFIX}/upkeeps").json() if u["title"] == title
            ][0]

        then = local_now() - dt.timedelta(days=5)
        receipted = client.post(
            f"{PREFIX}/upkeeps/{upkeep['id']}/receipts",
            json={"receipted_at": then.isoformat()},
        )
        assert receipted.status_code == 201

        body = client.get(f"{PREFIX}/catchup").json()
        expected = (local_now() - then).days
        assert body["away_days"] == expected


class TestPendingItems:
    def test_an_active_goal_and_a_thought_are_pending(self, client: TestClient) -> None:
        goal = client.post(
            f"{PREFIX}/goals",
            json={"title": "learn guitar", "current_focus": "chord shapes"},
        )
        assert goal.status_code == 201
        capture = client.post(f"{PREFIX}/capture", json={"text": "maybe a case"})
        assert capture.status_code == 201

        body = client.get(f"{PREFIX}/catchup").json()
        assert [g["title"] for g in body["goals"]] == ["learn guitar"]
        assert body["goals"][0]["current_focus"] == "chord shapes"
        assert [t["text"] for t in body["thoughts"]] == ["maybe a case"]

    def test_a_dismissed_thought_drops_out_but_stays(self, client: TestClient) -> None:
        """Clearing is a drop-out of the STRIP only: the Thought is kept
        forever as the user's words (provenance), visible at
        include_resolved=true."""
        capture = client.post(f"{PREFIX}/capture", json={"text": "sorting jars"})
        thought_id = capture.json()["thought"]["id"]

        resolved = client.post(
            f"{PREFIX}/thoughts/{thought_id}/resolve", json={"choice": "dismissed"}
        )
        assert resolved.status_code == 200

        body = client.get(f"{PREFIX}/catchup").json()
        assert all(t["thought_id"] != thought_id for t in body["thoughts"])

        all_thoughts = client.get(f"{PREFIX}/thoughts?include_resolved=true").json()
        assert any(t["id"] == thought_id for t in all_thoughts)

    def test_a_paused_goal_drops_out_of_pending(self, client: TestClient) -> None:
        goal = client.post(f"{PREFIX}/goals", json={"title": "resting goal"})
        goal_id = goal.json()["id"]
        paused = client.post(f"{PREFIX}/goals/{goal_id}/pause")
        assert paused.status_code == 200

        body = client.get(f"{PREFIX}/catchup").json()
        assert all(g["goal_id"] != goal_id for g in body["goals"])
        # ...it is resting, not gone: the Goals page still shows it.
        goals = client.get(f"{PREFIX}/goals").json()
        assert any(g["id"] == goal_id for g in goals)


class TestAck:
    def test_ack_is_idempotent_and_persists_one_row(
        self, client: TestClient, engine: Engine
    ) -> None:
        day = local_today().isoformat()
        first = client.post(f"{PREFIX}/catchup/ack", json={"day": day})
        second = client.post(f"{PREFIX}/catchup/ack", json={"day": day})
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == {"acked": True}
        assert second.json() == {"acked": True}

        with engine.connect() as connection:
            row_count: int = connection.execute(
                text("SELECT count(*) FROM life.catchup_ack WHERE day = :day"),
                {"day": day},
            ).scalar_one()
        assert row_count == 1

    def test_ack_flips_acked_today_until_the_day_turns(
        self, client: TestClient
    ) -> None:
        assert client.get(f"{PREFIX}/catchup").json()["acked_today"] is False
        client.post(f"{PREFIX}/catchup/ack", json={"day": local_today().isoformat()})
        assert client.get(f"{PREFIX}/catchup").json()["acked_today"] is True


class TestVocabulary:
    def test_the_catchup_contract_carries_no_judgment_words(
        self, client: TestClient
    ) -> None:
        """Exact vocab guard, aimed at the away strip: no habit/streak/
        overdue word can appear in the OpenAPI contract of this router."""
        spec = client.get("/openapi.json").json()
        catchup_text = str(spec["paths"]["/api/v1/life/catchup"])
        assert "overdue" not in catchup_text
        assert "streak" not in catchup_text
        assert "backlog" not in catchup_text
