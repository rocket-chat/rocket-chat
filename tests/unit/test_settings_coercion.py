"""How each setting kind turns a submitted value into a stored one, or says why it cannot."""

from __future__ import annotations

from typing import Any

import pytest
from config_engine.registry import SettingSpec, _coerce


def _spec(kind: str, **overrides: Any) -> SettingSpec:
    fields: dict[str, Any] = {
        "key": "k",
        "kind": kind,
        "default": None,
        "scope": "org",
        "consumer": "x",
        "description": "d",
    }
    fields.update(overrides)
    return SettingSpec(**fields)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("bool", True, True),
        ("bool", " FALSE ", False),
        ("int", "7", 7),
        ("float", "2.5", 2.5),
        ("str", "  padded ", "padded"),
        ("list", "a, b ,,c", ["a", "b", "c"]),
        ("list", [" x ", "", "y"], ["x", "y"]),
        ("dict", {"k": 1}, {"k": 1}),
    ],
)
def test_values_are_converted_to_their_kind(kind: str, value: Any, expected: Any) -> None:
    assert _coerce(_spec(kind, default=expected), value) == expected


@pytest.mark.parametrize(
    ("kind", "value", "message"),
    [
        ("bool", "yes", "must be true or false"),
        ("int", True, "must be a int"),
        ("int", "many", "must be a int"),
        ("float", None, "must not be empty"),
        ("str", 3, "must be text"),
        ("list", [1, 2], "must be a list of text values"),
        ("dict", [], "must be an object"),
        ("matrix", 1, "unsupported kind matrix"),
    ],
)
def test_malformed_values_say_why(kind: str, value: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _coerce(_spec(kind, default=1), value)


def test_numbers_respect_their_bounds() -> None:
    bounded = _spec("int", default=5, minimum=1, maximum=10)
    assert _coerce(bounded, 10) == 10
    with pytest.raises(ValueError, match="at least 1"):
        _coerce(bounded, 0)
    with pytest.raises(ValueError, match="at most 10"):
        _coerce(bounded, 11)


def test_enums_accept_only_their_choices() -> None:
    spec = _spec("enum", default="a", choices=("a", "b"))
    assert _coerce(spec, "b") == "b"
    with pytest.raises(ValueError, match="must be one of: a, b"):
        _coerce(spec, "c")


def test_an_optional_setting_may_be_cleared() -> None:
    assert _coerce(_spec("str", default=None), None) is None
