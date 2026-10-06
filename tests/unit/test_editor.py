"""Unit tests for AST and block-replacement editor."""

import pytest
from agent_core.editor import create_file_diff, replace_block


def test_replace_block_exact() -> None:
    source = "def add(a, b):\n    return a + b\n"
    target = "    return a + b"
    replacement = "    # add with validation\n    return int(a) + int(b)"
    result = replace_block(source, target, replacement)

    assert "return int(a) + int(b)" in result
    assert "return a + b" not in result


def test_replace_block_whitespace_tolerance() -> None:
    source = "def compute():   \n    x = 10   \n    return x\n"
    target = "def compute():\n    x = 10"
    replacement = "def compute():\n    x = 20"
    result = replace_block(source, target, replacement)

    assert "x = 20" in result
    assert "x = 10" not in result


def test_replace_block_ambiguity_raises_value_error() -> None:
    source = "item = 1\nitem = 1\n"
    target = "item = 1"
    replacement = "item = 2"

    with pytest.raises(ValueError, match="matches 2 exact locations"):
        replace_block(source, target, replacement)


def test_replace_block_not_found_raises_value_error() -> None:
    source = "def foo():\n    pass\n"
    target = "def bar():\n    pass"
    replacement = "def baz():\n    pass"

    with pytest.raises(ValueError, match="Target block not found"):
        replace_block(source, target, replacement)


def test_create_file_diff() -> None:
    orig = "line 1\nline 2\nline 3\n"
    mod = "line 1\nline 2 mod\nline 3\nline 4\n"
    diff = create_file_diff("calc.py", orig, mod)

    assert diff.path == "calc.py"
    assert diff.additions == 2  # line 2 mod, line 4
    assert diff.deletions == 1  # line 2
    assert "--- a/calc.py" in diff.diff_content
    assert "+++ b/calc.py" in diff.diff_content
