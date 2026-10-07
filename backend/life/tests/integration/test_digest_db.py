"""test_digest_db.py — the digest, end to end, on the real migrations.

The claims: the digest's fact-gathering picks the day the same ways the
today route does (a dated open action due today IS a do-now; a done action
is not), `--dry-run` runs the CLI path with no ntfy configuration at all,
and `send_digest`'s push lands on the configured server/topic with the
day's facts in the body. The network is always a MockTransport — a db test
and a live push are different suites.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import httpx
import pytest
from cli import main as cli_main
from fastapi.testclient import TestClient
from finance.db import get_sessionmaker
from main import create_app

from life.api.digest import gather_digest_facts, send_digest
from life.api.ntfy import NtfyConfig
from life.local import local_today

pytestmark = pytest.mark.db

PREFIX = "/api/v1/life"

SERVER = "https://ntfy.example"
TOPIC = "lifeos-digest"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _dated_action(client: TestClient, title: str, due: dt.date) -> None:
    created = client.post(
        f"{PREFIX}/actions", json={"text": title, "due_date": due.isoformat()}
    )
    assert created.status_code == 201


class TestGather:
    def test_an_open_action_due_today_is_a_do_now_fact(
        self, client: TestClient
    ) -> None:
        title = "put the desk together"
        _dated_action(client, title, local_today())

        factory = get_sessionmaker()
        with factory() as session, session.begin():
            facts = gather_digest_facts(session, today_date=local_today())
        assert title in facts.do_now

    def test_a_done_action_is_not_pushed_as_a_fact(self, client: TestClient) -> None:
        created = client.post(
            f"{PREFIX}/actions",
            json={
                "text": "cancel the appointment",
                "due_date": local_today().isoformat(),
            },
        )
        assert created.status_code == 201
        action_id = created.json()["id"]
        done = client.post(f"{PREFIX}/actions/{action_id}/done")
        assert done.status_code == 200

        factory = get_sessionmaker()
        with factory() as session, session.begin():
            facts = gather_digest_facts(session, today_date=local_today())
        assert "cancel the appointment" not in facts.do_now


class TestDryRun:
    def test_the_cli_prints_today_s_facts_and_exits_zero(
        self, client: TestClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        title = "register the bike"
        _dated_action(client, title, local_today())

        code = cli_main(["digest", "--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert "== title ==" in out
        assert title in out
        # Facts exist on the board today, so the empty-day line must NOT be
        # composed here — the two messages are mutually exclusive by design.
        assert "Nothing assigned to today" not in out


class TestSend:
    def test_send_digest_posts_the_facts_to_the_configured_target(
        self, client: TestClient
    ) -> None:
        title = "fix the door"
        _dated_action(client, title, local_today())

        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        config = NtfyConfig(server_url=SERVER, topic=TOPIC)
        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            message = send_digest(config, get_sessionmaker(), http=http)

        assert len(seen) == 1
        assert str(seen[0].url) == f"{SERVER}/{TOPIC}"
        assert title in seen[0].content.decode()
        assert seen[0].headers["Title"] == message.title
