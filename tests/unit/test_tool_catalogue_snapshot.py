"""The model-facing tool catalogue is derived from the FastMCP handlers.

``tests/golden/tool_catalogue.json`` is the reviewed catalogue the model sees. A change to a
tool's name, description or parameters must update the snapshot in the same commit, so a
prompt-visible change is always a visible diff rather than an accident of a signature edit.
Regenerate with ``UPDATE_TOOL_CATALOGUE=1 uv run pytest tests/unit/test_tool_catalogue_snapshot.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from agent_core.tools.registry import ToolRegistry

SNAPSHOT = Path(__file__).parent.parent / "golden" / "tool_catalogue.json"


def _catalogue() -> list[dict[str, Any]]:
    return [
        {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters_json_schema,
        }
        for definition in ToolRegistry().get_tool_definitions()
    ]


def test_tool_catalogue_matches_the_reviewed_snapshot() -> None:
    current = _catalogue()
    if os.getenv("UPDATE_TOOL_CATALOGUE"):
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    expected = json.loads(SNAPSHOT.read_text())
    assert {t["name"]: t for t in current} == {t["name"]: t for t in expected}


def test_every_registered_tool_is_in_the_catalogue_and_documented() -> None:
    registry = ToolRegistry()
    registered = {tool.name for tool in asyncio.run(registry.mcp.list_tools())}
    definitions = {d.name: d for d in registry.get_tool_definitions()}
    assert registered == set(definitions)
    for name, definition in definitions.items():
        assert definition.description.strip(), f"{name} has no description"
        for field, schema in definition.parameters_json_schema.get("properties", {}).items():
            assert schema.get("description"), f"{name}.{field} has no description"
