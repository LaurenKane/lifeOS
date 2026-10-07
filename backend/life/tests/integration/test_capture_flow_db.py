"""test_capture_flow_db.py — capture, end to end, on the real migrations.

Drives the real FastAPI app against the migrated `life` schema and asserts on
honest captured shapes. This is the acceptance core of the life module: the
capture must never block, every auto-decision must carry its undo, and a
loosely-phrased upkeep must ask rather than record (discovery Q21b)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from main import create_app

from life.local import local_today

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


class TestPlainThought:
    def test_a_capture_with_no_markers_rests_in_the_inbox(
        self, client: TestClient
    ) -> None:
        response = client.post(f"{PREFIX}/capture", json={"text": "maybe learn guitar"})
        assert response.status_code == 201
        body = response.json()
        assert body["created_action"] is None
        assert body["created_receipt_id"] is None
        assert body["upkeep_choice"] is None
        assert body["thought"]["resolved_kind"] is None

        inbox = client.get(f"{PREFIX}/thoughts").json()
        assert any(t["text"] == "maybe learn guitar" for t in inbox)


class TestDatedCapture:
    def test_tomorrow_creates_an_action_with_a_planned_date(
        self, client: TestClient
    ) -> None:
        today = local_today()
        response = client.post(
            f"{PREFIX}/capture", json={"text": "buy bedsheets tomorrow"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["created_action"] is not None
        action = body["created_action"]
        tomorrow = today + dt.timedelta(days=1)
        assert action["planned_date"] == tomorrow.isoformat()
        assert action["text"] == "buy bedsheets"
        assert body["thought"]["resolved_kind"] == "action"

    def test_a_planned_tomorrow_action_is_not_on_todays_board(
        self, client: TestClient
    ) -> None:
        board = client.get(f"{PREFIX}/today").json()
        planned_tomorrow = [a for a in board["do_now"] if a["text"] == "buy bedsheets"]
        assert planned_tomorrow == []  # planned tomorrow ≠ today's board
        # ...but an action with today's date lands on it, among the do-now:
        client.post(f"{PREFIX}/capture", json={"text": "put laundry away today"})
        board = client.get(f"{PREFIX}/today").json()
        assert any(a["text"] == "put laundry away" for a in board["do_now"])

    def test_urgent_marker_creates_an_urgent_action(self, client: TestClient) -> None:
        response = client.post(
            f"{PREFIX}/capture", json={"text": "call the bank urgent"}
        )
        body = response.json()
        assert body["created_action"] is not None
        assert body["created_action"]["urgent"] is True

    def test_undo_reverts_a_capture_created_action(self, client: TestClient) -> None:
        response = client.post(
            f"{PREFIX}/capture", json={"text": "return the library book tomorrow"}
        )
        thought_id = response.json()["thought"]["id"]
        undo = client.delete(f"{PREFIX}/capture/{thought_id}")
        assert undo.status_code == 200
        inbox = client.get(f"{PREFIX}/thoughts").json()
        assert any(t["id"] == thought_id and t["resolved_kind"] is None for t in inbox)


class TestUpkeepReceipt:
    def _ensure_upkeep(self, client: TestClient) -> None:
        # Each test method starts on a clean database (the truncated fixture),
        # so the ask/receipt tests create the upkeep they ask about.
        client.post(
            f"{PREFIX}/upkeeps", json={"title": "clean bathroom", "aim_days": 7}
        )

    def test_a_named_upkeep_with_no_verb_asks_not_records(
        self, client: TestClient
    ) -> None:
        """Q21b: no impact (but a proposal) from a loosely-phrased capture."""
        self._ensure_upkeep(client)
        response = client.post(f"{PREFIX}/capture", json={"text": "bathroom"})
        body = response.json()
        assert body["created_receipt_id"] is None
        assert body["upkeep_choice"] is not None
        assert body["upkeep_choice"]["title"] == "clean bathroom"

    def test_a_confident_receipt_records_and_carries_undo(
        self, client: TestClient
    ) -> None:
        self._ensure_upkeep(client)
        response = client.post(
            f"{PREFIX}/capture", json={"text": "cleaned the bathroom"}
        )
        body = response.json()
        assert body["created_receipt_id"] is not None
        assert body["thought"]["resolved_kind"] == "upkeep"

        # Last-done is a derivation over receipts; refetch shows it.
        upkeeps = client.get(f"{PREFIX}/upkeeps").json()
        bathroom = [u for u in upkeeps if u["title"] == "clean bathroom"][0]
        assert bathroom["last_done_at"] is not None

        thought_id = body["thought"]["id"]
        undo = client.delete(f"{PREFIX}/capture/{thought_id}")
        assert undo.status_code == 200
        upkeeps = client.get(f"{PREFIX}/upkeeps").json()
        bathroom = [u for u in upkeeps if u["title"] == "clean bathroom"][0]
        assert bathroom["last_done_at"] is None  # the receipt is gone

    def test_resolve_receipt_choice_on_an_asked_match(self, client: TestClient) -> None:
        self._ensure_upkeep(client)
        ask = client.post(f"{PREFIX}/capture", json={"text": "clean bathroom"})
        thought_id = ask.json()["thought"]["id"]
        upkeep_id = ask.json()["upkeep_choice"]["upkeep_id"]
        resolve = client.post(
            f"{PREFIX}/thoughts/{thought_id}/resolve",
            json={"choice": "upkeep", "upkeep_id": upkeep_id},
        )
        assert resolve.status_code == 200

    def test_resolve_action_choice_creates_an_action(self, client: TestClient) -> None:
        ask = client.post(f"{PREFIX}/capture", json={"text": "do the laundry"})
        thought_id = ask.json()["thought"]["id"]
        resolve = client.post(
            f"{PREFIX}/thoughts/{thought_id}/resolve", json={"choice": "action"}
        )
        body = resolve.json()
        assert body["created_action"]["text"] == "do the laundry"
        assert body["thought"]["resolved_kind"] == "action"


class TestUpkeepAim:
    def test_aim_days_is_stored_and_returned(self, client: TestClient) -> None:
        response = client.post(
            f"{PREFIX}/upkeeps", json={"title": "bins", "aim_days": 3}
        )
        assert response.json()["aim_days"] == 3
        # The truly overdue invariant: an aim is NOT a due date — the summary
        # carries `next_opportunity` (derived) and nothing about being late.
        assert "overdue" not in response.text
