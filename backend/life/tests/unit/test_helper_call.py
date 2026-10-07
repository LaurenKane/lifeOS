"""Unit tests for the helper seam: the request shape and the error contract.

Every test rides on `httpx.MockTransport`, so nothing here opens a socket —
the acceptance is what goes on the wire and when `HelperError` raises, not
a live provider.
"""

from __future__ import annotations

import httpx
import pytest

from life.api.helper import HelperConfig, HelperError, chat_completion

BASE = "https://llm.example/v1"
MODEL = "test-model"
PROMPT = "sort these thoughts"


def config(api_key: str = "") -> HelperConfig:
    return HelperConfig(base_url=BASE, api_key=api_key, model=MODEL)


def _ok_response() -> httpx.Response:
    body = {"choices": [{"message": {"role": "assistant", "content": '[{"x": 1}]'}}]}
    return httpx.Response(200, json=body)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


class TestRequest:
    def test_a_200_returns_the_first_choices_content(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok_response()

        with _client(httpx.MockTransport(handler)) as http:
            answer = chat_completion(config(), prompt=PROMPT, http=http)
        assert answer == '[{"x": 1}]'

    def test_the_url_is_base_plus_chat_completions_and_the_body_is_minimal(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok_response()

        with _client(httpx.MockTransport(handler)) as http:
            chat_completion(config(), prompt=PROMPT, http=http)
        assert str(seen[0].url) == f"{BASE}/chat/completions"
        assert seen[0].read() is not None
        body = seen[0].content.decode()
        assert '"temperature":0}' in body
        assert PROMPT in body

    def test_a_trailing_slash_on_the_base_url_is_not_doubled(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok_response()

        trailing = HelperConfig(base_url=f"{BASE}/", model=MODEL)
        with _client(httpx.MockTransport(handler)) as http:
            chat_completion(trailing, prompt=PROMPT, http=http)
        assert str(seen[0].url) == f"{BASE}/chat/completions"

    def test_a_bearer_header_is_sent_only_with_a_key(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok_response()

        with _client(httpx.MockTransport(handler)) as http:
            chat_completion(config(api_key="sk_test_123"), prompt=PROMPT, http=http)
            chat_completion(config(), prompt=PROMPT, http=http)
        assert seen[0].headers["Authorization"] == "Bearer sk_test_123"
        assert "Authorization" not in seen[1].headers


class TestErrors:
    def test_a_non_2xx_raises_helper_error_with_an_excerpt(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="bad key")

        with _client(httpx.MockTransport(handler)) as http:
            with pytest.raises(HelperError) as exc:
                chat_completion(config(), prompt=PROMPT, http=http)
        assert "401" in str(exc.value)
        assert "bad key" in str(exc.value)

    def test_a_non_2xx_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, text="down")

        with _client(httpx.MockTransport(handler)) as http:
            with pytest.raises(HelperError):
                chat_completion(config(), prompt=PROMPT, http=http)
        assert calls == 1

    def test_a_transport_error_once_then_success_is_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("no route to helper")
            return _ok_response()

        with _client(httpx.MockTransport(handler)) as http:
            answer = chat_completion(config(), prompt=PROMPT, http=http)
        assert calls == 2
        assert answer == '[{"x": 1}]'

    def test_a_transport_error_twice_raises_helper_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("no route to helper")

        with _client(httpx.MockTransport(handler)) as http:
            with pytest.raises(HelperError) as exc:
                chat_completion(config(), prompt=PROMPT, http=http)
        assert calls == 2
        assert "no route to helper" in str(exc.value)

    def test_a_body_without_the_chat_shape_raises_helper_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"no": "choices"})

        with _client(httpx.MockTransport(handler)) as http:
            with pytest.raises(HelperError):
                chat_completion(config(), prompt=PROMPT, http=http)

    def test_a_non_text_content_raises_helper_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"choices": [{"message": {"content": 17}}]}
            return httpx.Response(200, json=body)

        with _client(httpx.MockTransport(handler)) as http:
            with pytest.raises(HelperError):
                chat_completion(config(), prompt=PROMPT, http=http)


class TestAvailability:
    def test_availability_means_a_base_url_and_nothing_else(self) -> None:
        assert HelperConfig(base_url=BASE).is_available is True
        assert HelperConfig(base_url="").is_available is False
