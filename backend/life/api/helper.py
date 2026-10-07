"""helper — the ONE HTTP seam for the inbox organizer.

The endpoint is ANY OpenAI-compatible chat-completions API (`LIFEOS_LLM_BASE_URL`
with an optional bearer key and model name). This module is the only place in
the life module that knows that; everything else deals in `HelperConfig`,
`chat_completion`, and the parsed suggestions.

Client injection, like ntfy: `chat_completion` takes `http: httpx.Client` and
sets no timeout itself: the caller's client owns the timeout policy.
`get_organize_client` exists as the route's dependency so tests can override
it with a `httpx.MockTransport` client without touching the real network.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from config import get_settings

__all__ = [
    "HelperConfig",
    "HelperError",
    "chat_completion",
    "get_organize_client",
]

#: The longest slice of an error response kept in the exception message.
_MAX_RESPONSE_EXCERPT: int = 200

#: The one retry the helper gets — on a TRANSPORT error only.
_MAX_TRANSPORT_ATTEMPTS: int = 2


class HelperError(Exception):
    """The helper refused or failed the call (non-2xx, transport, or a
    response that is not the chat-completions shape)."""


@dataclass(frozen=True)
class HelperConfig:
    """Where and how the organizer's one call goes.

    `api_key` is empty for an unauthenticated endpoint and never read from a
    repository file: it arrives through `LIFEOS_LLM_API_KEY` in the deployer's
    own environment (SAFETY.md — credentials never live in this repository).
    """

    #: Chat-completions base URL, e.g. "https://llm.example/v1" (no trailing
    #: slash needed; `/chat/completions` is appended).
    base_url: str
    #: Bearer token, or "" when the endpoint needs none.
    api_key: str = ""
    #: Model name sent in the request body.
    model: str = ""

    @classmethod
    def from_settings(cls) -> HelperConfig:
        """The configured helper, or empty strings when unset."""
        settings = get_settings()
        return cls(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
        )

    @property
    def is_available(self) -> bool:
        """True when a base URL is set: then the feature exists; empty means
        the helper is simply ABSENT — the frontend renders no button, the
        feature is off rather than off-and-waiting."""
        return bool(self.base_url)


def chat_completion(config: HelperConfig, *, prompt: str, http: httpx.Client) -> str:
    """One chat-completions POST; the first choice's message content, or raise.

    One retry on an httpx transport error (a dropped connection is the one
    failure a retry can actually fix); no retry is spent on a non-2xx — a
    refusing endpoint is refusing, and doubling the spend does not change its
    mind. A response missing the choices/message shape raises `HelperError`
    too: the boundary accepts only an answer, not a plausible apology.

    Raises:
        HelperError: On a non-2xx response (with a short excerpt of the body),
            on a transport error after the one retry, or on a response whose
            JSON lacks `choices[0].message.content`.
    """
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    headers: dict[str, str] = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    last_transport_error: httpx.HTTPError | None = None
    for _attempt in range(1, _MAX_TRANSPORT_ATTEMPTS + 1):
        try:
            response = http.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            last_transport_error = exc
            continue  # one quiet retry, the poke, not the policy
        _raise_unless_ok(response, url)
        return _content(response)
    assert last_transport_error is not None
    raise HelperError(
        f"helper request failed after {_MAX_TRANSPORT_ATTEMPTS} attempts:"
        f"{last_transport_error}"
    )


def _raise_unless_ok(response: httpx.Response, url: str) -> None:
    if not (200 <= response.status_code <= 299):
        excerpt = response.text[:_MAX_RESPONSE_EXCERPT]
        raise HelperError(
            f"helper returned {response.status_code} for {url}: {excerpt}"
        )


def _content(response: httpx.Response) -> str:
    try:
        body: object = response.json()
        content = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (ValueError, LookupError, TypeError) as exc:
        raise HelperError(f"helper response is not a chat completion: {exc}") from exc
    if not isinstance(content, str):
        raise HelperError("helper response content is not text")
    return content


def get_organize_client() -> Iterator[httpx.Client]:
    """The route's HTTP client dependency — a FastAPI Depends seam.

    The route never constructs a client inline: tests override this dependency
    with a `MockTransport` client (`app.dependency_overrides`), so a db test
    and a live provider call stay different suites. 20 s is the whole call's
    patience; the helper answers in that window or does not answer.
    """
    with httpx.Client(timeout=20.0) as client:
        yield client
