"""test_organize_db.py — the helper route, end to end, on the real migrations.

The claims: the organizer sends exactly the unresolved pile (capped), proves
suggestion ids against the rows it sent (junk ids dropped, nothing applied),
maps a helper failure to an honest 502, and the GET's availability flag is
absent-by-default — off, not off-and-waiting. The network is never real: the
route's client dependency is overridden with a MockTransport client.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol

import httpx
import pytest
from config import get_settings
from fastapi.testclient import TestClient
from main import create_app

from life.api.helper import get_organize_client

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"


class EnvSetter(Protocol):
    def __call__(
        self, *, base_url: str = "", api_key: str = "", model: str = ""
    ) -> None: ...


def canned_json(thought_id: int, junk_id: int, wrong_word_id: int) -> str:
    """A helper answer: one valid suggestion, one hallucinated id, one
    choice outside the pile's vocabulary — the shape a model actually emits."""
    return json.dumps(
        [
            {
                "thought_id": thought_id,
                "choice": "action",
                "due_date": "2026-10-09",
                "urgent": False,
                "why": "the text names a concrete chore",
            },
            {"thought_id": junk_id, "choice": "action", "why": "hallucinated"},
            {"thought_id": wrong_word_id, "choice": "goal", "why": "wrong word"},
        ]
    )


def canned_completion(content: str) -> httpx.Response:
    """A 200 chat-completions envelope around `content` — the helper's only
    observable output."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def helper_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[EnvSetter]:
    """Set the helper's env vars and refresh the cached settings; restore
    the cache afterwards so other tests see the default (absent) helper."""

    def set_env(
        base_url: str = "https://llm.example/v1",
        api_key: str = "",
        model: str = "test-model",
    ) -> None:
        monkeypatch.setenv("LIFEOS_LLM_BASE_URL", base_url)
        monkeypatch.setenv("LIFEOS_LLM_API_KEY", api_key)
        monkeypatch.setenv("LIFEOS_LLM_MODEL", model)
        get_settings.cache_clear()

    set_env()
    yield set_env
    get_settings.cache_clear()


@pytest.fixture
def resolved_mix(client: TestClient) -> dict[str, int]:
    """Three thoughts: two resting, one explicitly resolved to `rests`."""
    ids: list[int] = []
    for text in ("maybe a case", "sort the jars", "tidy the desk"):
        created = client.post(f"{PREFIX}/capture", json={"text": text})
        assert created.status_code == 201
        ids.append(int(created.json()["thought"]["id"]))
    resolved = client.post(
        f"{PREFIX}/thoughts/{ids[2]}/resolve", json={"choice": "rests"}
    )
    assert resolved.status_code == 200
    return {"a": ids[0], "b": ids[1], "resolved": ids[2]}


class TestOrganizeRoute:
    def test_suggestions_carry_only_proven_ids_and_nothing_is_applied(
        self,
        client: TestClient,
        helper_env: EnvSetter,
        resolved_mix: dict[str, int],
    ) -> None:
        content = canned_json(
            resolved_mix["a"], junk_id=999999, wrong_word_id=resolved_mix["b"]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return canned_completion(content)

        app = create_app()
        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as http:
            app.dependency_overrides[get_organize_client] = lambda: http
            with TestClient(app) as scoped:
                response = scoped.post(f"{PREFIX}/thoughts/organize")

        assert response.status_code == 200
        suggestions = response.json()["suggestions"]
        # One junk id dropped, one off-vocabulary choice dropped.
        assert len(suggestions) == 1
        assert suggestions[0]["thought_id"] == resolved_mix["a"]
        assert suggestions[0]["choice"] == "action"
        assert suggestions[0]["due_date"] == "2026-10-09"
        assert suggestions[0]["urgent"] is False
        assert len(suggestions[0]["why"]) <= 120

        # Nothing was applied server-side: the pile still holds both.
        pile = client.get(f"{PREFIX}/thoughts").json()
        assert {row["id"] for row in pile} == {
            resolved_mix["a"],
            resolved_mix["b"],
        }

    def test_only_unresolved_thoughts_are_sent(
        self,
        client: TestClient,
        helper_env: EnvSetter,
        resolved_mix: dict[str, int],
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return canned_completion("[]")

        app = create_app()
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app.dependency_overrides[get_organize_client] = lambda: http
            with TestClient(app) as scoped:
                response = scoped.post(f"{PREFIX}/thoughts/organize")

        assert response.status_code == 200
        assert response.json() == {"suggestions": []}
        sent = seen[0].content.decode()
        # The resolved thought's text is nowhere in the prompt; the two
        # resting ones are.
        assert "tidy the desk" not in sent
        assert "maybe a case" in sent
        assert "sort the jars" in sent
        assert str(resolved_mix["resolved"]) not in sent

    def test_a_silent_helper_is_a_502_that_names_itself(
        self,
        client: TestClient,
        helper_env: EnvSetter,
        resolved_mix: dict[str, int],
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to helper")

        app = create_app()
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app.dependency_overrides[get_organize_client] = lambda: http
            with TestClient(app) as scoped:
                response = scoped.post(f"{PREFIX}/thoughts/organize")

        assert response.status_code == 502
        assert "the helper did not answer" in response.json()["detail"]

        # The failed call left the pile exactly as it was.
        pile = client.get(f"{PREFIX}/thoughts").json()
        assert len(pile) == 2

    def test_an_unconfigured_helper_makes_the_button_absent(
        self, client: TestClient
    ) -> None:
        response = client.get(f"{PREFIX}/thoughts")
        assert response.status_code == 200
        assert response.headers["X-Helper-Available"] == "false"

    def test_a_configured_helper_makes_the_button_present(
        self, client: TestClient, helper_env: EnvSetter
    ) -> None:
        response = client.get(f"{PREFIX}/thoughts")
        assert response.headers["X-Helper-Available"] == "true"

    def test_an_empty_pile_runs_no_call_at_all(
        self, client: TestClient, helper_env: EnvSetter
    ) -> None:
        # A module-scoped app keeps this test honest only if the pile is
        # genuinely emptied; resolve everything this module created.
        pile = client.get(f"{PREFIX}/thoughts").json()
        for row in pile:
            resolved = client.post(
                f"{PREFIX}/thoughts/{row['id']}/resolve", json={"choice": "dismissed"}
            )
            assert resolved.status_code == 200

        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return canned_completion("[]")

        app = create_app()
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            app.dependency_overrides[get_organize_client] = lambda: http
            with TestClient(app) as scoped:
                response = scoped.post(f"{PREFIX}/thoughts/organize")

        assert response.status_code == 200
        assert response.json() == {"suggestions": []}
        assert called is False
