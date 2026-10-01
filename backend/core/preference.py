from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Preference:
    """A user preference setting.

    Stored as (key, value) pairs. Keys should be namespaced (e.g. "finance.default_currency").
    Values are stored as strings; callers handle type conversion.
    """

    key: str
    value: str
    category: str = "general"  # e.g. "finance", "ui", "general"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            msg = "Preference key must not be empty"
            raise ValueError(msg)

    def __str__(self) -> str:
        return f"Preference(key='{self.key}', value='{self.value}', category='{self.category}')"

    def __repr__(self) -> str:
        return f"Preference(key='{self.key}', value='{self.value}', category='{self.category}')"


class PreferencesStore:
    """In-memory preference store for development; replace with DB/persistence later."""

    def __init__(self) -> None:
        self._store: Dict[str, Preference] = {}

    def set(self, preference: Preference) -> None:
        self._store[preference.key] = preference

    def get(self, key: str, default: str | None = None) -> str | None:
        preference = self._store.get(key)
        if preference is None:
            return default
        return preference.value

    def get_as(self, key: str, cast_type: type, default: object = None) -> object:
        value = self.get(key, str(default) if default is not None else "")
        try:
            return cast_type(value)
        except (ValueError, TypeError):
            return default
