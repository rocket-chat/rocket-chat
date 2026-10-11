"""The complexity limits apply to every source file; only tests and scripts are exempt."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXEMPT_PATTERNS = {"tests/**", "scripts/**"}


def test_no_source_file_is_exempt_from_the_size_and_complexity_limits() -> None:
    config = tomllib.loads((ROOT / ".ruff.toml").read_text())
    exempted = set(config["lint"].get("per-file-ignores", {}))

    assert exempted <= EXEMPT_PATTERNS, (
        f"Split the function instead of exempting {sorted(exempted - EXEMPT_PATTERNS)}"
    )


def test_the_limits_stay_at_their_agreed_values() -> None:
    lint = tomllib.loads((ROOT / ".ruff.toml").read_text())["lint"]

    assert lint["mccabe"]["max-complexity"] <= 15
    assert lint["pylint"]["max-statements"] <= 60
    assert lint["pylint"]["max-branches"] <= 15
    assert {"C901", "PLR0915", "PLR0912"} <= set(lint["select"])
