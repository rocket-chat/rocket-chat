"""Tool output stays within budget whatever its shape, and says how to get what it hid."""

from __future__ import annotations

import pytest
from agent_core.editor import replace_block
from agent_core.tools import tier1, tier2
from agent_core.tools.clamp import DEFAULT_MAX_BYTES, clamp_output
from sandbox_driver.fake_driver import FakeSandboxDriver

from specifications.interfaces.sandbox import WorkspaceSpec


def test_few_very_long_lines_stay_under_the_byte_cap() -> None:
    minified = "".join(f"{'x' * 10_000}\n" for _ in range(280))

    clamped = clamp_output(minified)

    assert len(clamped.encode()) <= DEFAULT_MAX_BYTES + 200
    assert "line cut: 10,000 characters" in clamped and "bytes hidden" in clamped


def test_the_marker_names_the_hidden_lines_and_how_to_get_them() -> None:
    text = "".join(f"line {n}\n" for n in range(1, 1_001))

    clamped = clamp_output(text, hint="read them with file_read start_line/end_line")

    assert "[lines 81-800 of 1,000 hidden; read them with file_read start_line/end_line]" in clamped
    assert clamped.startswith("line 1\n") and clamped.rstrip().endswith("line 1000")


async def _driver(content: str) -> FakeSandboxDriver:
    driver = FakeSandboxDriver()
    await driver.ensure_workspace(WorkspaceSpec("s", "o", "u", "i"))
    await driver.start_sandbox("s")
    await driver.write_file("s", "/workspace/big.py", content)
    return driver


async def test_file_read_pages_with_line_numbers() -> None:
    driver = await _driver("".join(f"value_{n} = {n}\n" for n in range(1, 1_001)))

    first = await tier2.file_read(driver, "s", "big.py")
    middle = await tier2.file_read(driver, "s", "big.py", start_line=401, end_line=403)
    plain = await tier2.file_read(driver, "s", "big.py", 999, None, line_numbers=False)

    assert first.startswith("     1\tvalue_1 = 1\n") and "   400\tvalue_400 = 400" in first
    assert "lines 1-400 of 1000; call file_read with start_line=401 to continue" in first
    assert middle.splitlines()[:3] == [
        "   401\tvalue_401 = 401",
        "   402\tvalue_402 = 402",
        "   403\tvalue_403 = 403",
    ]
    assert plain.startswith("value_999 = 999\nvalue_1000 = 1000\n") and "end of file" in plain


def test_a_block_copied_with_its_line_numbers_still_edits_the_file() -> None:
    source = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    pasted = "     4\tdef b():\n     5\t    return 2\n"
    replacement = "     4\tdef b():\n     5\t    return 3\n"

    assert replace_block(source, pasted, replacement) == (
        "def a():\n    return 1\n\ndef b():\n    return 3\n"
    )


@pytest.mark.parametrize("start, expected", [(0, "characters 0-20,000"), (20_000, "end of page")])
def test_web_pages_are_read_in_windows(start: int, expected: str) -> None:
    text = "w" * 35_000

    window = tier1._page(text, "https://docs.example", start, 50_000)

    assert expected in window and len(window) < 21_000
