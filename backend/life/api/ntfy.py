"""ntfy — the one HTTP seam for push.

ntfy (https://ntfy.sh or a self-hosted server) takes a POST: topic as the
URL's last path segment, the message in the body, a title in the `Title`
header, and an optional bearer token when the server requires one. This
module is the only place that knows that; everything else deals in
`DigestMessage` and `NtfyConfig`.

There are no retries and no timeout set here on purpose: the caller owns the
client (and therefore the retry and timeout policy), and a digest that fails
to send is reported to the CLI's stderr, not silently retried by cron.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from config import get_settings

from life.domain.services.digest import DigestMessage

__all__ = ["NtfyConfig", "NtfyError", "post_to_ntfy"]

#: The longest slice of an error response kept in the exception message. A
#: four-hundred-line HTML error page is not a message; the first two hundred
#: characters name the problem.
_MAX_RESPONSE_EXCERPT: int = 200


class NtfyError(Exception):
    """The ntfy server refused or failed the POST (non-2xx, or transport)."""


@dataclass(frozen=True)
class NtfyConfig:
    """Where and how a digest is pushed.

    `token` is empty for an open server and never read from a repository
    file: it arrives through `LIFEOS_NTFY_TOKEN` in the deployer's own
    environment (SAFETY.md — credentials never live in this repository).
    """

    #: Server base URL, e.g. "https://ntfy.sh" (no trailing slash needed).
    server_url: str
    #: The topic name, as configured in the user's phone app.
    topic: str
    #: Access token, or "" when the server needs none.
    token: str = ""

    @classmethod
    def from_settings(cls) -> NtfyConfig:
        """The configured push target, or empty strings when unset."""
        settings = get_settings()
        return cls(
            server_url=settings.NTFY_SERVER_URL,
            topic=settings.NTFY_TOPIC,
            token=settings.NTFY_TOKEN,
        )

    @property
    def is_configured(self) -> bool:
        """True when both a server and a topic are set."""
        return bool(self.server_url) and bool(self.topic)


def post_to_ntfy(
    message: DigestMessage, config: NtfyConfig, *, http: httpx.Client
) -> None:
    """POST one message to the configured topic.

    Args:
        message: The composed digest.
        config: Server, topic and optional token.
        http: The caller's client — this module sets no timeout of its own
            and performs no retries.

    Raises:
        NtfyError: On a non-2xx response (with a short excerpt of the body)
            or on any `httpx` transport error (with its message).
    """
    url = f"{config.server_url.rstrip('/')}/{config.topic}"
    # The title carries the middle dot of "LifeOS · Wed 7 Oct", and httpx
    # encodes *str* header values as ASCII — it would reject the dot outright.
    # UTF-8 bytes are the wire format ntfy's server (Go, byte-wise) reads as
    # one title string.
    headers: dict[bytes, bytes] = {b"Title": message.title.encode("utf-8")}
    if config.token:
        headers[b"Authorization"] = f"Bearer {config.token}".encode("ascii")

    try:
        response = http.post(url, headers=headers, content=message.body)
    except httpx.HTTPError as exc:
        raise NtfyError(f"ntfy request failed: {exc}") from exc

    if not (200 <= response.status_code <= 299):
        excerpt = response.text[:_MAX_RESPONSE_EXCERPT]
        raise NtfyError(f"ntfy returned {response.status_code}: {excerpt}")
