"""The settings reference is generated from the registry and must stay in sync."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_settings_reference_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location(
        "generate_settings_reference", ROOT / "scripts" / "generate_settings_reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = (ROOT / "doc" / "administer" / "settings-reference.md").read_text()
    assert committed == module.render(), (
        "doc/administer/settings-reference.md is stale; run "
        "`uv run python scripts/generate_settings_reference.py`"
    )
