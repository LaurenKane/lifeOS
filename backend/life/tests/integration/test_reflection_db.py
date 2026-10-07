"""test_reflection_db.py — the "Looking back" read, end to end.

What is certain: counts of done Actions per active Goal and receipts per
active Upkeep, inside a rolling 30-day window; a goal with nothing done is
absent rather than represented at zero; a paused goal rests off the board; and
no judgment word can reach the wire (vocabulary guard over the actual
response body). On the real migrations, through the app's own routes.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from main import create_app

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def a_goal(client: TestClient, title: str) -> int:
    created = client.post(f"{PREFIX}/goals", json={"title": title})
    assert created.status_code == 201
    goal_id: int = created.json()["id"]
    return goal_id


def a_done_step(client: TestClient, goal_id: int, text: str) -> None:
    created = client.post(f"{PREFIX}/actions", json={"text": text, "goal_id": goal_id})
    assert created.status_code == 201
    marked = client.post(f"{PREFIX}/actions/{created.json()['id']}/done")
    assert marked.status_code == 200


class TestCountsInWindow:
    def test_two_steps_one_step_and_one_receipt_count_up(
        self, client: TestClient
    ) -> None:
        """done_at is now for a just-completed Action, so the three facts are
        in-window by construction; counts 2/1/1 are the whole response."""
        guitar = a_goal(client, "learn guitar")
        garden = a_goal(client, "tend the garden")
        upkeep = client.post(
            f"{PREFIX}/upkeeps", json={"title": "water the plants", "aim_days": 3}
        )
        assert upkeep.status_code == 201
        upkeep_id: int = upkeep.json()["id"]

        a_done_step(client, guitar, "put the desk together")
        a_done_step(client, guitar, "change strings")
        a_done_step(client, garden, "sow the herbs")
        # `receipted_at` absent means now — but the endpoint still requires a
        # JSON body, so the receipt is recorded with an empty payload.
        receipted = client.post(f"{PREFIX}/upkeeps/{upkeep_id}/receipts", json={})
        assert receipted.status_code == 201

        got = client.get(f"{PREFIX}/reflection")
        # An empty summary is a normal answer — 200, not an error short-cut.
        assert got.status_code == 200
        body = got.json()
        assert body["window_days"] == 30
        assert [(g["goal_id"], g["count"]) for g in body["goals"]] == [
            (guitar, 2),
            (garden, 1),
        ]
        # Newest first, at most three, the actual words the user wrote.
        assert body["goals"][0]["done_texts"] == [
            "change strings",
            "put the desk together",
        ]
        assert body["goals"][0]["title"] == "learn guitar"
        assert body["goals"][0]["area"] is None
        assert [(u["upkeep_id"], u["count"]) for u in body["upkeeps"]] == [
            (upkeep_id, 1)
        ]

    def test_a_goal_with_nothing_done_is_absent(self, client: TestClient) -> None:
        """Zero is not a data point: an idle goal is missing from the list,
        and its absence is the whole answer — silence stays silent."""
        quiet = a_goal(client, "quiet goal")
        other = a_goal(client, "working goal")
        a_done_step(client, other, "sharpen the pencil")

        body = client.get(f"{PREFIX}/reflection").json()
        assert [g["goal_id"] for g in body["goals"]] == [other]
        assert all(g["goal_id"] != quiet for g in body["goals"])

    def test_a_paused_goal_rests_off_the_board(self, client: TestClient) -> None:
        """Paused is resting, per Q8's own vocabulary: even with a done step
        inside the window, a paused goal stays off the reflection while it
        stays on its own page."""
        resting = a_goal(client, "resting guitar")
        a_done_step(client, resting, "strum once")
        paused = client.post(f"{PREFIX}/goals/{resting}/pause")
        assert paused.status_code == 200

        body = client.get(f"{PREFIX}/reflection").json()
        assert all(g["goal_id"] != resting for g in body["goals"])
        goals = client.get(f"{PREFIX}/goals").json()
        assert any(g["id"] == resting for g in goals)

    def test_nothing_done_anywhere_reads_as_empty_arrays(
        self, client: TestClient
    ) -> None:
        """The empty month is a normal 200 with empty lists, not an error or
        a sentinel."""
        body = client.get(f"{PREFIX}/reflection")
        assert body.status_code == 200
        assert body.json() == {"window_days": 30, "goals": [], "upkeeps": []}


class TestVocabulary:
    def test_the_reflection_body_carries_no_judgment_words(
        self, client: TestClient
    ) -> None:
        """The guard aims at the actual wire body, not the OpenAPI path map:
        no habit-style judgment vocabulary in any key, title or count label."""
        guitar = a_goal(client, "learn guitar")
        a_done_step(client, guitar, "put the desk together")
        upkeep = client.post(
            f"{PREFIX}/upkeeps", json={"title": "water the plants", "aim_days": 3}
        )
        assert upkeep.status_code == 201
        receipted = client.post(
            f"{PREFIX}/upkeeps/{upkeep.json()['id']}/receipts", json={}
        )
        assert receipted.status_code == 201

        response = client.get(f"{PREFIX}/reflection")
        assert response.status_code == 200
        text = response.text
        for word in ("percent", "overdue", "streak", "backlog", "rate"):
            assert word not in text, f"the reflection body named {word!r}"
