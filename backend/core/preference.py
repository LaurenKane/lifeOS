"""User preferences - (key, value) pairs, namespaced by module.

Values are stored as strings and cast on read. That keeps the storage layer
trivially portable (one column type) and makes a bad value a read-time
error rather than a write-time crash, which matters because the alternative is
refusing to save an unrelated setting because one of them was malformed.

No `Any`: `get_as` takes a `Callable[[str], T]` so the caller keeps the type it
asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

# Preference keys are dotted and module-prefixed, e.g. "finance.default_currency".
_SEPARATOR = "."


@dataclass(frozen=True, order=True)
class Preference:
    """A single stored preference. Immutable; changing one means writing another."""

    key: str
    value: str
    category: str = "general"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            msg = "Preference key must not be empty"
            raise ValueError(msg)
        if self.key.startswith(_SEPARATOR) or self.key.endswith(_SEPARATOR):
            msg = f"Preference key must be namespaced, got {self.key!r}"
            raise ValueError(msg)

    @property
    def namespace(self) -> str:
        """The module prefix of the key, e.g. "finance" for "finance.currency"."""
        return self.key.partition(_SEPARATOR)[0]

    def __str__(self) -> str:
        return f"Preference({self.category}:{self.key}={self.value!r})"


class PreferencesStore:
    """An in-memory preference store.

    Deliberately in-memory for M0: this is the seam, not the persistence. A
    process restart loses the values, which is acceptable until the table
    exists, and preferable to pretending otherwise.
    """

    def __init__(self) -> None:
        self._store: dict[str, Preference] = {}

    def set(self, preference: Preference) -> None:
        self._store[preference.key] = preference

    def get(self, key: str, default: str | None = None) -> str | None:
        preference = self._store.get(key)
        return default if preference is None else preference.value

    def has(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> bool:
        """Remove a key. Returns whether anything was removed."""
        return self._store.pop(key, None) is not None

    def all_in(self, namespace: str) -> list[Preference]:
        """Every preference under a namespace, key-sorted."""
        prefix = f"{namespace}{_SEPARATOR}"
        return sorted(
            (p for p in self._store.values() if p.key.startswith(prefix)),
            key=lambda p: p.key,
        )

    def get_as(self, key: str, cast: Callable[[str], T], default: T) -> T:
        """Read a preference through a cast, falling back to `default`.

        The cast is a callable rather than a type so `bool` is not silently
        wrong: `bool("false")` is True. Pass a real parse function.
        """
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return cast(raw)
        except (ValueError, TypeError):
            return default
