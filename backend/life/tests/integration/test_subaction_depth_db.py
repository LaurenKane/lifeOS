"""test_subaction_depth_db.py — the one-level rule, enforced by the trigger.

The rule: "packing" holds "clothes", "charger"; a subaction can never hold
subactions. Enforced by the `trg_action_depth` trigger in migration 0001 — a
CHECK cannot read another row, so this is the one place the invariant CAN be
enforced, and it is enforced there rather than in a handler.

The handler's 422s are asserted via the API; the trigger itself is asserted
with raw SQL, because nothing else exercises it on its own."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

PREFIX = "/api/v1/life"

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_parent(client: TestClient) -> int:
    created = client.post(f"{PREFIX}/actions", json={"text": "pack for the trip"})
    body = created.json()
    return cast("int", body["id"])


class TestOneLevelOfSubactions:
    def test_a_subaction_can_be_added(self, client: TestClient) -> None:
        parent = _make_parent(client)
        sub = client.post(
            f"{PREFIX}/actions/{parent}/subactions", json={"text": "clothes"}
        )
        assert sub.status_code == 201

    def test_a_grandchild_is_refused_by_the_api_with_a_clear_reason(
        self, client: TestClient
    ) -> None:
        parent = _make_parent(client)
        sub = client.post(
            f"{PREFIX}/actions/{parent}/subactions", json={"text": "clothes"}
        ).json()
        grandchild = client.post(
            f"{PREFIX}/actions/{sub['id']}/subactions", json={"text": "socks"}
        )
        assert grandchild.status_code == 422
        assert "one level" in grandchild.json()["detail"]


class TestTheTriggerItself:
    def test_the_trigger_refuses_a_grandchild_at_the_database(
        self, engine: Engine
    ) -> None:
        with engine.connect() as connection:
            with connection.begin():
                parent = cast(
                    "int",
                    connection.execute(
                        text(
                            "INSERT INTO action (title) VALUES"
                            " ('pack for the trip') RETURNING id"
                        )
                    ).scalar_one(),
                )
                child = cast(
                    "int",
                    connection.execute(
                        text(
                            "INSERT INTO action (title, parent_action_id)"
                            " VALUES ('clothes', :parent) RETURNING id"
                        ),
                        {"parent": parent},
                    ).scalar_one(),
                )
                with pytest.raises(ProgrammingError):
                    connection.execute(
                        text(
                            "INSERT INTO action (title, parent_action_id)"
                            " VALUES ('socks', :child)"
                        ),
                        {"child": child},
                    )
                    # The DDL attempt aborts the enclosing transaction, so
                    # nothing needs explicit cleanup.

    def test_a_self_parenting_action_is_refused(self, engine: Engine) -> None:
        with engine.connect() as connection:
            try:
                with connection.begin():
                    connection.execute(
                        text(
                            "INSERT INTO action (id, title, parent_action_id)"
                            " VALUES (7, 'circular', 7)"
                        )
                    )
            except ProgrammingError:
                pass
            else:
                pytest.fail("an action parented itself and the trigger said nothing")
