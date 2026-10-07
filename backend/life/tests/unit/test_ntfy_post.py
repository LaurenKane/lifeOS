"""Unit tests for post_to_ntfy: headers, status handling — no network.

Every test rides on `httpx.MockTransport`, so nothing here opens a socket;
the acceptance is the request's shape and the error contract, not a live
server.
"""

from __future__ import annotations

import httpx
import pytest

from life.api.ntfy import NtfyConfig, NtfyError, post_to_ntfy
from life.domain.services.digest import DigestMessage

SERVER = "https://ntfy.example"
TOPIC = "lifeos-digest"

MESSAGE = DigestMessage(title="LifeOS · Wed 7 Oct", body="3 things on today")


def config(token: str = "") -> NtfyConfig:
    return NtfyConfig(server_url=SERVER, topic=TOPIC, token=token)


def _mock(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


class TestEnvelope:
    def test_a_200_send_posts_to_the_server_then_the_topic(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with _mock(httpx.MockTransport(handler)) as http:
            post_to_ntfy(MESSAGE, config(), http=http)
        assert len(seen) == 1
        assert str(seen[0].url) == "https://ntfy.example/lifeos-digest"

    def test_the_title_travels_in_the_title_header(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with _mock(httpx.MockTransport(handler)) as http:
            post_to_ntfy(MESSAGE, config(), http=http)
        assert seen[0].headers["Title"] == "LifeOS · Wed 7 Oct"

    def test_the_body_is_the_message_body(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with _mock(httpx.MockTransport(handler)) as http:
            post_to_ntfy(MESSAGE, config(), http=http)
        assert seen[0].content == b"3 things on today"

    def test_a_trailing_slash_on_the_server_url_is_not_doubled(self) -> None:
        seen: list[httpx.Request] = []
        trailing = NtfyConfig(server_url=f"{SERVER}/", topic=TOPIC)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with _mock(httpx.MockTransport(handler)) as http:
            post_to_ntfy(MESSAGE, trailing, http=http)
        assert str(seen[0].url) == f"{SERVER}/{TOPIC}"


class TestAuthorization:
    def test_a_bearer_header_is_sent_only_with_a_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with _mock(httpx.MockTransport(handler)) as http:
            post_to_ntfy(MESSAGE, config(token="tk_test_123"), http=http)
            post_to_ntfy(MESSAGE, config(token=""), http=http)
        assert seen[0].headers["Authorization"] == "Bearer tk_test_123"
        assert "Authorization" not in seen[1].headers


class TestErrors:
    def test_a_non_2xx_raises_ntfy_error_with_an_excerpt(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden topic")

        with _mock(httpx.MockTransport(handler)) as http:
            with pytest.raises(NtfyError) as exc:
                post_to_ntfy(MESSAGE, config(), http=http)
        assert "403" in str(exc.value)
        assert "forbidden topic" in str(exc.value)

    def test_a_long_error_body_is_truncated_to_200_characters(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="x" * 1000)

        with _mock(httpx.MockTransport(handler)) as http:
            with pytest.raises(NtfyError) as exc:
                post_to_ntfy(MESSAGE, config(), http=http)
        message = str(exc.value)
        assert len(message) < 500
        assert "x" * 201 not in message

    def test_a_transport_error_raises_ntfy_error_not_httpx(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _mock(httpx.MockTransport(handler)) as http:
            with pytest.raises(NtfyError) as exc:
                post_to_ntfy(MESSAGE, config(), http=http)
        assert "connection refused" in str(exc.value)
