"""Section-mapping tests — the form field that attributes Revolut sections.

`section_account_ids` arrives as a form string carrying a JSON object like
`{"deposit": 7}`. Parsing it is a boundary decision: a section attributed to
the wrong account writes money to the wrong account, and the batch would then
remember the mistake as the mapping. So anything that is not a known section
to a positive account id is a 400, never a guess.

Synthetic data only; no database.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from finance.api.routes.imports import _parse_section_account_ids


def _rejected(raw: str) -> HTTPException:
    """The 400 the field's refusal carries, or a failure naming the input."""
    with pytest.raises(HTTPException) as caught:
        _parse_section_account_ids(raw)
    error = caught.value
    assert error.status_code == 400, f"{raw!r} refused with {error.status_code}"
    return error


class TestAbsentMapping:
    def test_none_means_no_mapping(self) -> None:
        """No field is the single-account case, not an error."""
        assert _parse_section_account_ids(None) is None

    def test_blank_means_no_mapping(self) -> None:
        """Whitespace-only is absent, not malformed."""
        assert _parse_section_account_ids("") is None
        assert _parse_section_account_ids("   ") is None


class TestValidMapping:
    def test_a_valid_object_maps_sections_to_accounts(self) -> None:
        """The case the whole field exists for."""
        assert _parse_section_account_ids('{"deposit": 7}') == {"deposit": 7}

    def test_keys_are_lowercased(self) -> None:
        """`"Deposit"` and `"deposit"` name the same section, not two."""
        assert _parse_section_account_ids('{"Deposit": 7}') == {"deposit": 7}
        assert _parse_section_account_ids('{"Account": 3, "deposit": 7}') == {
            "account": 3,
            "deposit": 7,
        }

    def test_an_empty_object_is_an_empty_mapping(self) -> None:
        """`{}` constrains nothing, so it refuses nothing either."""
        assert _parse_section_account_ids("{}") == {}


class TestMalformedJson:
    def test_truncated_json_is_a_400(self) -> None:
        """A half-written object is data, not a guessable one."""
        error = _rejected('{"deposit": ')
        assert "JSON" in error.detail

    def test_a_non_object_is_a_400(self) -> None:
        """A list, a string or a bare number names no section at all."""
        for raw in ('[["deposit", 7]]', '"deposit"', "7", "null", "true"):
            error = _rejected(raw)
            assert "object" in error.detail, raw


class TestUnknownSection:
    def test_an_unknown_section_is_a_400(self) -> None:
        """A section the statement never prints cannot be attributed."""
        error = _rejected('{"savings": 7}')
        assert "savings" in error.detail


class TestBadAccountId:
    def test_a_bool_is_not_an_account_id(self) -> None:
        """`True == 1` in Python, so an explicit check carries this case."""
        _rejected('{"deposit": true}')

    def test_a_string_is_not_an_account_id(self) -> None:
        _rejected('{"deposit": "7"}')

    def test_a_float_is_not_an_account_id(self) -> None:
        _rejected('{"deposit": 7.0}')

    def test_zero_and_negatives_are_not_account_ids(self) -> None:
        _rejected('{"deposit": 0}')
        _rejected('{"deposit": -3}')

    def test_null_is_not_an_account_id(self) -> None:
        _rejected('{"deposit": null}')
